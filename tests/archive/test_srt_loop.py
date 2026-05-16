import time
import logging
from app.core.config_store import ConfigStore
from app.services.media import MediaController
from app.services.telemetry import TelemetryService
from app.services.events import EventLog

logging.basicConfig(level=logging.INFO)
# Uncomment for heavy debug:
# logging.getLogger("app.services.gst_runtime").setLevel(logging.DEBUG)

def main():
    print("Initializing DanteBridge Test Script...")
    store = ConfigStore()
    telemetry = TelemetryService()
    events = EventLog()
    media = MediaController(telemetry)

    print("Configuring local loopback...")
    # Overwrite the srt_transports to just have a local loopback on port 12000
    store.update({
        "srt_transports": [
            {
                "id": "tx-loop",
                "name": "Local Loopback TX",
                "direction": "tx",
                "mode": "listener",
                "port": 12000,
                "latency_ms": 20,
                "encode_group_ids": ["g1"],
                "enabled": True
            },
            {
                "id": "rx-loop",
                "name": "Local Loopback RX",
                "direction": "rx",
                "mode": "caller",
                "host": "127.0.0.1",
                "port": 12000,
                "latency_ms": 20,
                "encode_group_ids": ["g1"], # Uses g1 channel mapping (Ch 1) for RX destination
                "enabled": True
            }
        ]
    })

    print("Starting master spine...")
    media.start_spine(store.config)
    print("Spine started.")

    print("\nStarting TX leg (listener)...")
    media.start_srt_transport(store.config, "tx-loop")
    time.sleep(1) # give the listener a second to bind
    
    print("\nStarting RX leg (caller)...")
    media.start_srt_transport(store.config, "rx-loop")
    time.sleep(1)
    
    print("\nPipeline is alive! Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(2)
            # Fetch some basic telemetry or just wait
            print("TX Loop stats: ", telemetry.srt_transports.get("tx-loop"))
            print("RX Loop stats: ", telemetry.srt_transports.get("rx-loop"))
    except KeyboardInterrupt:
        print("\nStopping...")
        media.stop_all()
        print("Done.")

if __name__ == "__main__":
    main()
