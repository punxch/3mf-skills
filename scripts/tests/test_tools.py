"""Run directly, optionally with --deps-dir DIR, from any working directory."""
import argparse
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--deps-dir', type=Path)
options, test_args = parser.parse_known_args()
SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
if options.deps_dir:
    sys.path.insert(0, str(options.deps_dir.resolve()))
import numpy as np
import mf3_tools.archive as archive

NS = archive.CORE
PN = archive.PRODUCTION
MESH = '''<mesh><vertices><vertex x="0" y="0" z="0"/>
<vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>
</vertices><triangles><triangle v1="0" v2="1" v3="2" paint_color="04443"/>
</triangles></mesh>'''


def model(shift=0, paint='04443', unit='millimeter'):
    return f'''<model xmlns="{NS}" unit="{unit}"><resources><object id="1">
{MESH.replace('04443', paint)}</object></resources><build>
<item objectid="1" transform="1 0 0 0 1 0 0 0 1 {shift} 0 0"/>
</build></model>'''.encode()


def write_fixture(path, entries=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('Metadata/保留说明.txt', '保持原样'.encode())
        for name, value in (entries or {'3D/3dmodel.model': model()}).items():
            z.writestr(name, value)


class ModelTests(unittest.TestCase):
    def test_chinese_path_and_topology_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'模型 含空格.3mf'
            write_fixture(path)
            result = archive.inspect_model(path, topology=True)
            self.assertEqual(result['dimensions_mm'], [1., 1., 0.])
            self.assertEqual(result['meshes'][0]['triangles'], 1)
            self.assertFalse(result['meshes'][0]['watertight'])
            self.assertIsNone(result['meshes'][0]['volume_mm3'])

    def test_external_component_units_and_multiple_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'nested.3mf'
            main = f'''<model xmlns="{NS}" xmlns:p="{PN}" unit="inch">
<resources><object id="7"><components>
<component objectid="1" p:path="Objects/piece.model" transform="0 1 0 -1 0 0 0 0 1 2 0 0"/>
</components></object></resources><build>
<item objectid="7" transform="1 0 0 0 1 0 0 0 1 1 0 0"/>
<item objectid="7" transform="1 0 0 0 1 0 0 0 1 2 0 0"/>
</build></model>'''
            write_fixture(path, {'3D/3dmodel.model': main, '3D/Objects/piece.model': model()})
            scene, _ = archive.load_scene(path)
            self.assertEqual(len(scene), 2)
            np.testing.assert_allclose(scene[0][2].vertices, [[76.2,0,0], [76.2,1,0], [75.2,0,0]])
            np.testing.assert_allclose(scene[1][2].vertices[:,0]-scene[0][2].vertices[:,0], 25.4)

    def test_package_root_relationship(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'root.3mf'
            rels = '<Relationships><Relationship Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/Models/main.model"/></Relationships>'
            write_fixture(path, {'_rels/.rels': rels, 'Models/main.model': model()})
            self.assertEqual(archive.load_scene(path)[0][0][0], 'Models/main.model')

    def test_invalid_geometry_and_cyclic_components(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bad.3mf'
            write_fixture(path, {'3D/3dmodel.model': model().replace(b'v3="2"', b'v3="9"')})
            with self.assertRaises(ValueError): archive.load_scene(path)
            cycle = f'<model xmlns="{NS}"><resources><object id="1"><components><component objectid="1"/></components></object></resources><build><item objectid="1"/></build></model>'
            write_fixture(path, {'3D/3dmodel.model': cycle})
            with self.assertRaisesRegex(ValueError, 'Cyclic'): archive.load_scene(path)

    def test_comparison_preserves_geometry_but_ignores_paint(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.3mf', Path(directory)/'b.3mf'
            write_fixture(a)
            write_fixture(b, {'3D/3dmodel.model': model(paint='2C')})
            self.assertTrue(archive.compare_geometry(a, b))
            write_fixture(b, {'3D/3dmodel.model': model(shift=2)})
            self.assertFalse(archive.compare_geometry(a, b))


class PaintAndSaveTests(unittest.TestCase):
    def test_untouched_split_paint_and_object_selection(self):
        blob = b'''<c:objects xmlns:c="test"><c:object id="1"><c:triangle v1="0" v2="1" v3="2" paint_color="04443"/></c:object>
<c:object id='2'><c:triangle v1='0' v2='1' v3='2' paint_color='8'/></c:object></c:objects>'''
        changed = archive.rewrite_triangle_paint(blob, [1], {1: '1C'}, object_id='2')
        self.assertEqual(changed, blob.replace(b" paint_color='8'", b' paint_color="1C"'))
        self.assertIn(b'paint_color="04443"', changed)
        self.assertEqual(archive.rewrite_triangle_paint(blob, [0,0], {1: '1C'}), blob)
        spaced = b'<triangle v1="0" v2="1" v3="2" paint_color="8" \n/>'
        self.assertEqual(archive.rewrite_triangle_paint(spaced, [1], {1:'1C'}), spaced.replace(b'paint_color="8"', b'paint_color="1C"'))

    def test_bad_selection_is_rejected(self):
        for selection in ([0,1], [], [1.5]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                archive.rewrite_triangle_paint(model(), selection, {1: '1C'})
        with self.assertRaises(ValueError): archive.rewrite_triangle_paint(model(), [1], {1: 'not-hex'})
        with self.assertRaises(ValueError): archive.rewrite_triangle_paint(model(), [1], {1: '1C'}, object_id='99')

    def test_save_preserves_unmodified_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'原模型.3mf', Path(directory)/'新版本.3mf'
            write_fixture(source)
            original = source.read_bytes()
            changed = archive.rewrite_triangle_paint(model(), [1], {1:'1C'})
            archive.write_archive(source, target, {'3D/3dmodel.model': changed})
            self.assertEqual(source.read_bytes(), original)
            with zipfile.ZipFile(target) as z:
                self.assertEqual(z.read('Metadata/保留说明.txt'), '保持原样'.encode())
                self.assertIn(b'paint_color="1C"', z.read('3D/3dmodel.model'))
            with self.assertRaises(ValueError): archive.write_archive(source, source, {})

    def test_failed_packaging_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'a.3mf', Path(directory)/'b.3mf'
            write_fixture(source)
            write_fixture(target, {'3D/3dmodel.model': model(shift=5)})
            saved = target.read_bytes()
            replacements = [{'missing.model': b'bad'}, {'3D/3dmodel.model': b'<broken'},
                            {'3D/3dmodel.model': f'<model xmlns="{NS}"><resources/><build/></model>'.encode()},
                            {'3D/3dmodel.model': model().replace(b'v3="2"', b'v3="9"')}]
            for replacement in replacements:
                with self.subTest(replacement=replacement), self.assertRaises((ValueError, ET.ParseError)):
                    archive.write_archive(source, target, replacement)
                self.assertEqual(target.read_bytes(), saved)
            with patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError('simulated write failure')):
                with self.assertRaises(OSError): archive.write_archive(source, target, {})
            self.assertEqual(target.read_bytes(), saved)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


class CommandTests(unittest.TestCase):
    def command(self, args, cwd):
        argv = [sys.executable, '-X', 'utf8', str(SCRIPTS/'cad.py')]
        if options.deps_dir: argv += ['--deps-dir', str(options.deps_dir.resolve())]
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        return subprocess.run(argv+args, cwd=cwd, env=environment, capture_output=True, text=True, encoding='utf8', timeout=60)

    def test_relative_inputs_outputs_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'中文模型.3mf'
            write_fixture(path)
            result = self.command(['inspect', path.name, '--output', '报告.json'], directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((Path(directory)/'报告.json').read_text(encoding='utf8'))['instance_count'], 1)
            self.assertTrue(Path(archive.__file__).resolve().is_relative_to(SCRIPTS))
            self.assertNotIn('cad_tools', sys.modules)

    def test_failure_and_comparison_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'a.3mf'
            write_fixture(path)
            saved = path.read_bytes()
            result = self.command(['inspect', 'a.3mf', '--output', 'a.3mf'], directory)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(self.command(['inspect', 'missing.3mf'], directory).returncode, 2)
            write_fixture(Path(directory)/'b.3mf', {'3D/3dmodel.model': model(shift=1)})
            self.assertEqual(self.command(['compare', 'a.3mf', 'b.3mf'], directory).returncode, 1)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *test_args])
