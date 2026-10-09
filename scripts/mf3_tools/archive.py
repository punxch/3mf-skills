"""Read geometry without assuming a Bambu object_1 filename or one object."""
from array import array
from dataclasses import dataclass
from pathlib import Path
import io
import json
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
import numpy as np

CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PRODUCTION = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
UNITS = {'micron': .001, 'millimeter': 1., 'centimeter': 10., 'meter': 1000., 'inch': 25.4, 'foot': 304.8}


def matrix(text=None):
    result = np.eye(4)
    if text:
        values = np.array([float(v) for v in text.split()])
        if values.size != 12 or not np.isfinite(values).all():
            raise ValueError('A 3MF transform must contain 12 finite numbers')
        result[:3, :] = values.reshape(4, 3).T
    return result


@dataclass
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray
    paint: list


def validate_mesh(mesh):
    if not len(mesh.faces):
        return
    if (not len(mesh.vertices) or not np.isfinite(mesh.vertices).all()
            or mesh.faces.min() < 0 or mesh.faces.max() >= len(mesh.vertices)):
        raise ValueError('Mesh contains invalid coordinates or triangle indices')


def read_document(blob):
    """Stream large mesh XML so million-face files do not build a full tree."""
    objects, build = {}, []
    current = None
    unit = 'millimeter'
    stack = []
    for event, el in ET.iterparse(io.BytesIO(blob), events=('start', 'end')):
        tag = el.tag[len(CORE)+2:] if el.tag.startswith('{'+CORE+'}') else ''
        if event == 'start':
            stack.append(el)
            if tag == 'model':
                unit = el.get('unit', 'millimeter')
            elif tag == 'object':
                current = {'id': el.get('id'), 'vertices': array('d'), 'faces': array('q'), 'paint': [], 'components': []}
            continue
        if current is not None:
            if tag == 'vertex':
                current['vertices'].extend(float(el.get(k)) for k in ('x', 'y', 'z'))
            elif tag == 'triangle':
                current['faces'].extend(int(el.get(k)) for k in ('v1', 'v2', 'v3'))
                current['paint'].append(el.get('paint_color', ''))
            elif tag == 'component':
                current['components'].append(dict(el.attrib))
            elif tag == 'object':
                current['mesh'] = Mesh(np.asarray(current.pop('vertices')).reshape(-1, 3).copy(),
                                       np.asarray(current.pop('faces'), dtype=np.int64).reshape(-1, 3).copy(), current.pop('paint'))
                validate_mesh(current['mesh'])
                objects[current['id']] = current
                current = None
        if tag == 'item':
            build.append(dict(el.attrib))
        stack.pop()
        if stack:
            stack[-1].remove(el)
        el.clear()
    if unit not in UNITS:
        raise ValueError(f'Unsupported 3MF unit: {unit}')
    return {'objects': objects, 'build': build, 'unit': unit}


def main_part(archive):
    if '_rels/.rels' in archive.namelist():
        for rel in ET.fromstring(archive.read('_rels/.rels')):
            if rel.get('Type', '').endswith('/3dmodel'):
                return rel.get('Target').lstrip('/')
    return '3D/3dmodel.model'


def load_scene(path):
    """Return build instances in world-space millimeters, including components."""
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f'Damaged ZIP entry: {bad}')
        docs = {}
        def document(name):
            if name not in docs:
                docs[name] = read_document(archive.read(name))
            return docs[name]
        root_name = main_part(archive)
        root = document(root_name)
        instances = []
        def visit(name, ident, transform, chain):
            key = (name, ident)
            if key in chain:
                raise ValueError('Cyclic 3MF component reference')
            doc = document(name)
            obj = doc['objects'][ident]
            mesh = obj['mesh']
            if len(mesh.faces):
                v = mesh.vertices @ transform[:3, :3].T + transform[:3, 3]
                if not np.isfinite(v).all():
                    raise ValueError('The build transform produced invalid coordinates')
                instances.append((name, ident, Mesh(v, mesh.faces, mesh.paint)))
            for component in obj['components']:
                reference = component.get(f'{{{PRODUCTION}}}path', name)
                if reference != name:
                    reference = posixpath.normpath(reference.lstrip('/') if reference.startswith('/') else posixpath.join(posixpath.dirname(name), reference))
                child = document(reference)
                scale = np.eye(4)
                scale[:3, :3] *= UNITS[child['unit']] / UNITS[doc['unit']]
                visit(reference, component['objectid'], transform @ matrix(component.get('transform')) @ scale, chain + (key,))
        unit_scale = np.eye(4)
        unit_scale[:3, :3] *= UNITS[root['unit']]
        for item in root['build']:
            visit(root_name, item['objectid'], unit_scale @ matrix(item.get('transform')), ())
        if not instances:
            raise ValueError('The 3MF build contains no mesh instances')
        config = json.loads(archive.read('Metadata/project_settings.config')) if 'Metadata/project_settings.config' in archive.namelist() else {}
        return instances, config.get('filament_colour', [])


def inspect_model(path, topology=False):
    instances, colors = load_scene(path)
    records = []
    for part, ident, mesh in instances:
        if not np.isfinite(mesh.vertices).all() or mesh.faces.min() < 0 or mesh.faces.max() >= len(mesh.vertices):
            raise ValueError(f'Invalid mesh in {part}, object {ident}')
        entry = {'part': part, 'object_id': ident, 'vertices': len(mesh.vertices), 'triangles': len(mesh.faces), 'bounds_mm': [mesh.vertices.min(0).tolist(), mesh.vertices.max(0).tolist()]}
        if topology:
            import trimesh
            model = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
            closed = bool(model.is_watertight)
            entry.update(watertight=closed, winding_consistent=bool(model.is_winding_consistent),
                         volume_mm3=float(model.volume) if closed else None)
        records.append(entry)
    bounds = np.array([r['bounds_mm'] for r in records])
    lower, upper = bounds[:, 0].min(0), bounds[:, 1].max(0)
    return {'file': str(Path(path).resolve()), 'instance_count': len(records), 'dimensions_mm': (upper-lower).tolist(), 'bounds_mm': [lower.tolist(), upper.tolist()], 'filament_colors': colors, 'meshes': records}


def rewrite_triangle_paint(blob, selection, codes, *, object_id=None):
    """Change whole-face Bambu paint, preserving all other bytes.

    Unmapped selection labels retain the exact old paint, including split-face
    encodings. When object_id is given, selection is local to that object.
    Self-closing triangle tags (with optional namespace prefix) are supported.
    """
    selection = np.asarray(selection)
    if selection.ndim != 1 or selection.dtype.kind not in 'iub':
        raise ValueError('Selection must be a one-dimensional integer or Boolean array')
    codes = {int(key): str(value) for key, value in codes.items()}
    if any(not re.fullmatch('[0-9A-Fa-f]+', value) for value in codes.values()):
        raise ValueError('Paint codes must be nonempty hexadecimal strings')
    start, end = 0, len(blob)
    if object_id is not None:
        objects = re.finditer(rb'<(?:[A-Za-z_][\w.-]*:)?object\b([^>]*)>(.*?)</(?:[A-Za-z_][\w.-]*:)?object\s*>', blob, re.DOTALL)
        found = []
        for obj in objects:
            ident = re.search(rb'''(?:^|\s)id\s*=\s*(["'])(.*?)\1''', obj[1])
            if ident and ident[2].decode('utf8') == str(object_id):
                found.append(obj.span(2))
        if len(found) != 1:
            raise ValueError(f'Expected one object with id {object_id}')
        start, end = found[0]
    target = blob[start:end]
    triangles = rb'<(?:[A-Za-z_][\w.-]*:)?triangle\b[^>]*?/>'
    if sum(1 for _ in re.finditer(triangles, target)) != len(selection):
        raise ValueError('Triangle count differs, or the XML uses unsupported triangle tags')
    paint_attribute = rb'''\s+paint_color\s*=\s*(["']).*?\1'''
    index = 0
    def replace(match):
        nonlocal index
        label = int(selection[index]); index += 1
        if label not in codes:
            return match[0]
        tag = re.sub(paint_attribute, b'', match[0])
        closing = re.search(rb'\s*/>$', tag).start()
        return tag[:closing] + b' paint_color="' + codes[label].encode('ascii') + b'"' + tag[closing:]
    changed = blob[:start] + re.sub(triangles, replace, target) + blob[end:]
    strip = lambda value: re.sub(paint_attribute, b'', value)
    if strip(blob) != strip(changed):
        raise AssertionError('Geometry changed while painting')
    return changed


def write_archive(source, output, replacements):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError('Save the modified model to a different file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as src:
        unknown = set(replacements) - set(src.namelist())
        if unknown:
            raise ValueError(f'Replacement entries are missing: {unknown}')
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name+'.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as dst:
                for entry in src.infolist():
                    dst.writestr(entry, replacements[entry.filename] if entry.filename in replacements else src.read(entry.filename))
            # Reject valid ZIP files whose 3MF build is empty, malformed or
            # points to missing/invalid geometry before replacing the output.
            load_scene(temporary)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def compare_geometry(first, second):
    """Compare mesh coordinates, topology and placements; colors may differ."""
    a, _ = load_scene(first)
    b, _ = load_scene(second)
    if len(a) != len(b):
        return False
    return all(np.array_equal(x[2].vertices, y[2].vertices) and np.array_equal(x[2].faces, y[2].faces) for x, y in zip(a, b))
