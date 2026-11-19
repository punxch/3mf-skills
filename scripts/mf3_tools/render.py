"""One depth-buffered renderer shared by drain and figure workflows."""
from pathlib import Path
import numpy as np


def render_mesh(vertices, faces, path, *, position=None, focus=None, scale=None,
                size=1000, colors=None, ids=False, up=(0, 0, 1), feature_angle=75,
                background=(.15, .19, .24), surface=(.62, .78, .88),
                ambient=.30, diffuse=.70, specular=.10, auto_orient=False):
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
    from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer, vtkRenderWindow, vtkWindowToImageFilter
    from vtkmodules.vtkIOImage import vtkPNGWriter
    import vtkmodules.vtkRenderingOpenGL2  # Register the offscreen backend.
    v, f = np.asarray(vertices), np.asarray(faces)
    if len(f) == 0 or size < 1:
        raise ValueError('Rendering needs triangles and a positive image size')
    if (v.ndim != 2 or v.shape[1] != 3 or f.ndim != 2 or f.shape[1] != 3
            or f.dtype.kind not in 'iu' or f.min() < 0 or f.max() >= len(v)
            or not np.isfinite(v).all()):
        raise ValueError('Invalid mesh arrays')
    if colors is not None and np.asarray(colors).shape != (len(f), 3):
        raise ValueError('Colors must contain one RGB triple per triangle')
    used = v[np.unique(f)]
    center = (used.min(0)+used.max(0))/2
    extent = np.linalg.norm(np.ptp(used, axis=0))
    focus = center if focus is None else np.asarray(focus)
    position = focus + np.array([1., -2., 1.]) * max(extent, 1) if position is None else np.asarray(position)
    scale = max(extent*.55, .001) if scale is None else scale
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError('Camera scale must be positive and finite')
    if np.linalg.norm(np.cross(position-focus, up)) < 1e-9:
        raise ValueError('Camera direction and up vector must not be parallel')
    points=vtkPoints();points.SetData(numpy_to_vtk(v,deep=True))
    cells=vtkCellArray();cells.SetCells(len(f),numpy_to_vtkIdTypeArray(np.column_stack([np.full(len(f),3),f]).astype(np.int64).ravel(),deep=True))
    poly=vtkPolyData();poly.SetPoints(points);poly.SetPolys(cells)
    if colors is not None:
        color_array=numpy_to_vtk(np.asarray(colors,dtype=np.uint8),deep=True);color_array.SetName('color');poly.GetCellData().SetScalars(color_array)
    normals=vtkPolyDataNormals();normals.SetInputData(poly);normals.SetFeatureAngle(feature_angle);normals.SplittingOn();normals.ConsistencyOn();normals.ComputePointNormalsOn()
    if auto_orient:normals.AutoOrientNormalsOn()
    mapper=vtkPolyDataMapper();mapper.SetInputConnection(normals.GetOutputPort())
    if colors is not None:mapper.SetScalarModeToUseCellData();mapper.SetColorModeToDirectScalars()
    actor=vtkActor();actor.SetMapper(mapper);prop=actor.GetProperty();prop.SetColor(*surface);prop.SetInterpolationToPhong();prop.SetAmbient(ambient);prop.SetDiffuse(diffuse);prop.SetSpecular(specular)
    if auto_orient:prop.SetSpecularPower(30)
    if ids:prop.LightingOff()
    renderer=vtkRenderer();renderer.SetBackground(*(0,0,0) if ids else background);renderer.AddActor(actor)
    camera=renderer.GetActiveCamera();camera.SetPosition(*position);camera.SetFocalPoint(*focus);camera.SetViewUp(*up);camera.ParallelProjectionOn();camera.SetParallelScale(scale)
    window=vtkRenderWindow();window.SetOffScreenRendering(1);window.SetSize(size,size);window.SetMultiSamples(0 if ids else 8);window.AddRenderer(renderer)
    try:
        renderer.ResetCameraClippingRange()
        window.Render()
        capture=vtkWindowToImageFilter();capture.SetInput(window);capture.ReadFrontBufferOff();capture.Update()
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        writer=vtkPNGWriter();writer.SetFileName(str(path));writer.SetInputConnection(capture.GetOutputPort());writer.Write()
        if writer.GetErrorCode() or not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise OSError(f'Could not write preview: {path}')
    finally:
        window.Finalize()
