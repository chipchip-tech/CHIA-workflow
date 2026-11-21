# render_gds.py - vẽ GDS ra PNG bằng KLayout (không cần màn hình), cho slide/video.
#   klayout -zz -r render_gds.py -rd gds=<file.gds> -rd png=<file.png> [-rd lyp=<file.lyp>]
import pya

lv = pya.LayoutView()
lv.load_layout(gds, True)  # noqa: F821 (biến từ -rd)
lyp = globals().get("lyp", "")
if lyp:
    lv.load_layer_props(lyp)
else:
    lv.add_missing_layers()
lv.max_hier()
lv.zoom_fit()
lv.save_image(png, 1600, 1600)  # noqa: F821
print("RENDER_DONE", png)  # noqa: F821
