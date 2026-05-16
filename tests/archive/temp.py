import ctypes  
from app.services.gst_runtime import CtypesGst  
from app.services.media_graph import MediaGraphBuilder  
from app.core.config import EndpointConfig, AudioConfig  
cfg = EndpointConfig(audio=AudioConfig(interface_driver='asio', interface_name='DVS', channel_count=2), sources=[], encode_groups=[], srt_transports=[])  
m = MediaGraphBuilder()  
plan = m.plan_spine(cfg)  
g = CtypesGst.load('gst-launch-1.0')  
ptr = ctypes.c_void_p()  
p = g.gst.gst_parse_launch(plan['gstreamer']['graph'].encode(), ctypes.byref(ptr))  
g.gst.gst_element_set_state(p, 4)  
bus = g.gst.gst_element_get_bus(p)  
g.gst.gst_bus_poll.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64]  
