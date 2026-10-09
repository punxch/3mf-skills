"""Portable entry point: inspect, compare and render 3MF files."""
import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile


def distinct_output(output, inputs):
    if output is not None and output.resolve() in {item.resolve() for item in inputs}:
        raise ValueError('输出路径不能与输入模型相同')


def main(argv=None):
    parser = argparse.ArgumentParser(description='独立的 3MF 检查、几何比较与结构预览工具')
    parser.add_argument('--deps-dir', type=Path, help='可选的第三方库目录；不改变当前工作目录')
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='查看对象、尺寸、网格和材料')
    inspect.add_argument('file', type=Path)
    inspect.add_argument('--topology', action='store_true', help='检查水密性、法线与体积')
    inspect.add_argument('--output', type=Path, help='保存 JSON 报告')
    compare = commands.add_parser('compare', help='比较世界坐标下的顶点和三角面，忽略颜色')
    compare.add_argument('first', type=Path)
    compare.add_argument('second', type=Path)
    render = commands.add_parser('render', help='生成统一表面颜色的 PNG 结构预览')
    render.add_argument('file', type=Path)
    render.add_argument('--output', type=Path, required=True)
    render.add_argument('--size', type=int, default=1000)
    render.add_argument('--view', choices=['oblique', 'front', 'back', 'left', 'right', 'top', 'bottom'], default='oblique')
    args = parser.parse_args(argv)
    if args.deps_dir:
        if not args.deps_dir.is_dir():
            raise ValueError(f'依赖目录不存在：{args.deps_dir}')
        sys.path.insert(0, str(args.deps_dir.resolve()))
    from mf3_tools.archive import inspect_model, compare_geometry, load_scene

    if args.command == 'inspect':
        distinct_output(args.output, [args.file])
        result = inspect_model(args.file, topology=args.topology)
        content = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content+'\n', encoding='utf8')
        print(content)
    elif args.command == 'compare':
        equal = compare_geometry(args.first, args.second)
        print('几何及位置完全一致' if equal else '几何或位置存在差异')
        return 0 if equal else 1
    else:
        import numpy as np
        from mf3_tools.render import render_mesh
        distinct_output(args.output, [args.file])
        if args.output.suffix.lower() != '.png':
            raise ValueError('预览输出必须使用 .png 后缀')
        if args.size <= 0:
            raise ValueError('图片尺寸必须为正整数')
        instances, _ = load_scene(args.file)
        vertices, faces, offset = [], [], 0
        for _, _, mesh in instances:
            vertices.append(mesh.vertices)
            faces.append(mesh.faces+offset)
            offset += len(mesh.vertices)
        vertices, faces = np.concatenate(vertices), np.concatenate(faces)
        options = {}
        if args.view != 'oblique':
            used = vertices[np.unique(faces)]
            center = (used.min(0)+used.max(0))/2
            distance = max(float(np.linalg.norm(np.ptp(used, axis=0))), 1.)
            directions = {'front': (0,-1,0), 'back': (0,1,0), 'left': (-1,0,0),
                          'right': (1,0,0), 'top': (0,0,1), 'bottom': (0,0,-1)}
            options = {'position': center+np.array(directions[args.view])*distance, 'focus': center}
            if args.view in ('top', 'bottom'):
                options['up'] = (0,1,0)
        render_mesh(vertices, faces, args.output, size=args.size, **options)
        print(args.output.resolve())
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ImportError as error:
        print(f'缺少依赖：{error}。请安装 scripts/requirements.txt，或指定 --deps-dir。', file=sys.stderr)
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        print(f'错误：{error}', file=sys.stderr)
        raise SystemExit(2)
