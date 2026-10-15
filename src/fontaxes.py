"""FreeType axis metadata, invoked only for a selected variable font; no third-party Python modules."""
import ctypes as c
import json
import sys

class Axis(c.Structure):
    _fields_ = [('name', c.c_char_p), ('minimum', c.c_long), ('default', c.c_long),
                ('maximum', c.c_long), ('tag', c.c_ulong), ('strid', c.c_uint)]
class Variation(c.Structure):
    _fields_ = [('count', c.c_uint), ('designs', c.c_uint), ('styles', c.c_uint),
                ('axes', c.POINTER(Axis)), ('named', c.c_void_p)]

ft = c.CDLL('libfreetype.so.6')
ft.FT_Init_FreeType.argtypes = [c.POINTER(c.c_void_p)]
ft.FT_New_Face.argtypes = [c.c_void_p, c.c_char_p, c.c_long, c.POINTER(c.c_void_p)]
ft.FT_Get_MM_Var.argtypes = [c.c_void_p, c.POINTER(c.POINTER(Variation))]
ft.FT_Done_MM_Var.argtypes = [c.c_void_p, c.POINTER(Variation)]
ft.FT_Done_Face.argtypes = [c.c_void_p]
ft.FT_Done_FreeType.argtypes = [c.c_void_p]
library = c.c_void_p()
if ft.FT_Init_FreeType(c.byref(library)):
    raise RuntimeError('FreeType could not initialize')
result = {}
try:
    for index in sys.argv[2:]:
        face = c.c_void_p()
        if ft.FT_New_Face(library, sys.argv[1].encode(), int(index), c.byref(face)):
            continue
        variation = c.POINTER(Variation)()
        try:
            if ft.FT_Get_MM_Var(face, c.byref(variation)) == 0:
                if variation.contents.count > 64:
                    raise ValueError('too many variation axes')
                axes = []
                for axis in variation.contents.axes[:variation.contents.count]:
                    tag = int(axis.tag).to_bytes(4, 'big').decode('ascii')
                    if len(tag) == 4 and all(32 <= ord(ch) <= 126 for ch in tag):
                        axes.append(dict(tag=tag, minimum=axis.minimum / 65536,
                            default=axis.default / 65536, maximum=axis.maximum / 65536))
                result[index] = axes
        finally:
            if variation:
                ft.FT_Done_MM_Var(library, variation)
            ft.FT_Done_Face(face)
finally:
    ft.FT_Done_FreeType(library)
print(json.dumps(result, allow_nan=False))
