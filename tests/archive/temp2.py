import ctypes
from app.services.gst_runtime import CtypesGst
from app.services.media_graph import MediaGraphBuilder
from app.core.config import EndpointConfig, AudioConfig

cfg = EndpointConfig(
    audio=AudioConfig(interface_driver='asio', interface_name='DVS', channel_count=2),
    sources=[],
    encode_groups=[],
    srt_transports=[]
)

m = MediaGraphBuilder()
plan = m.plan_spine(cfg)
g = CtypesGst.load('gst-launch-1.0')
ptr = ctypes.c_void_p()
p = g.gst.gst_parse_launch(plan['gstreamer']['graph'].encode(), ctypes.byref(ptr))

err = ctypes.c_void_p()
if ptr.value:
    error_msg = ctypes.c_char_p(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value)
    print("Parse error ptr:", error_msg.value)

g.gst.gst_element_set_state(p, 4)
bus = g.gst.gst_element_get_bus(p)
g.gst.gst_bus_poll.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64]
msg = g.gst.gst_bus_poll(bus, 2 | 4, ctypes.c_uint64(2000000000).value)
if msg:
    print(g.message_info(msg))
else:
    print('no msg')
