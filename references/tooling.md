# 工具与依赖

## 环境与路径

已验证环境为 Python 3.12。`scripts/requirements.txt` 列出通用工具所需的 NumPy、trimesh 和 VTK 版本；几何建模可能需要的其他库按当前任务选择，不把项目的整个依赖目录打包进 Skill。

使用已有解释器及已安装依赖，或在调用方项目的虚拟环境中安装：

```text
python -m pip install -r <skill-dir>/scripts/requirements.txt
```

若第三方库安装在自定义目录，将 `--deps-dir <目录>` 放在子命令前。仅向当前 Python 进程的搜索路径加入该目录，不改环境配置或当前工作目录。输入和输出的相对路径始终相对于调用时的工作目录；跨目录调用优先使用绝对路径。

## 命令

```text
python <skill-dir>/scripts/cad.py --help
python <skill-dir>/scripts/cad.py inspect <输入.3mf> --topology --output <报告.json>
python <skill-dir>/scripts/cad.py render <输入.3mf> --view back --output <后视图.png>
python <skill-dir>/scripts/cad.py render <输入.3mf> --view top --size 1200 --output <顶视图.png>
python <skill-dir>/scripts/cad.py compare <原始.3mf> <修改后.3mf>
python <skill-dir>/scripts/cad.py --deps-dir <依赖目录> inspect <输入.3mf>
```

`inspect` 读取实际 build 实例、单位和组件变换。`--topology` 增加水密性、法线一致性和有向体积检查；开放网格的体积返回 null。这些字段是检查结果，不表示模型一定适合打印。

`render` 的视角是世界坐标方向：front 为 −Y、back 为 +Y、left 为 −X、right 为 +X、top 为 +Z。模型摆放方向可能不等于人物解剖意义上的前后。默认 oblique；输出必须是 PNG。该命令只画结构，不解码复杂的面内分色。

`compare` 忽略颜色，按实例顺序精确比较世界坐标顶点和三角面索引。退出码 0 表示一致，1 表示不同，2 表示输入或运行错误。重新网格化或重新排序也会被判为不同，不能据此直接认定外形变化。其他命令成功返回 0，输入/运行错误返回 2。

## 从任务脚本调用

将技能目录的 `scripts` 加入当前进程的 `sys.path`，导入 `mf3_tools`。它与原项目可能存在的 `cad_tools` 名称分开，不含项目路径发现逻辑。

| 接口 | 输入与行为 |
| --- | --- |
| `read_document(blob)` | 读取一份模型 XML，返回单位、按对象 ID 索引的网格、组件及 build 条目；顶点是对象局部坐标 |
| `load_scene(path)` | 返回 `(instances, filament_colors)`；每个实例为 `(包内路径, 对象ID, Mesh)`，顶点已变换到世界坐标毫米 |
| `inspect_model(path, topology=False)` | 返回对象、尺寸、材料和可选拓扑检查的字典 |
| `compare_geometry(first, second)` | 按上述严格比较语义返回布尔值 |
| `rewrite_triangle_paint(blob, selection, codes, object_id=None)` | 按面片标签写入整面的 Bambu 涂色；未映射的标签保持不变 |
| `write_archive(source, output, replacements)` | 按包内路径替换指定条目，临时写入并检查实际几何引用后原子替换输出；拒绝覆盖源文件 |
| `render_mesh(vertices, faces, output, ...)` | 接受任意网格数组，可指定相机、每面 RGB、缩放和分辨率；不读取原项目缓存 |

`Mesh` 含 `vertices`、`faces`、`paint`。不要混用局部坐标和世界坐标。`load_scene` 会把一个资源的多个 build 实例分别返回，不能把这些重复实例当作原始资源面片编号。

`write_archive` 保留未指定条目的内容，只替换已经存在的条目，不创建新的资源关系。需要新增资源时，另外构造完整的 OPC/3MF 关系并验证；不要丢掉原包中的打印设置、缩略图和作者信息。

工具不解释全部 3MF 扩展，例如非三角网格表示或加密几何。读不到实际 build 网格时会报错，不把这种文件当作空白模型重新写入。

## 自检

```text
python <skill-dir>/scripts/tests/test_tools.py --deps-dir <依赖目录> -v
```

测试在临时目录构造小模型，覆盖组件、单位、摆放、区域分色保留和打包失败保护；不需要地漏或 Saber 文件。
