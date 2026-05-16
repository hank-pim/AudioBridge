@echo off
set /p GRAPH=<run_spine.txt
gst-launch-1.0 %GRAPH%
