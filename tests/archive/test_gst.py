import subprocess
graph = open("run_spine.txt").read().strip()
cmd = ["gst-launch-1.0", *graph.split(" ")]
subprocess.run(cmd)
