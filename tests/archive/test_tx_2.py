import time
import logging
from app.core.config_store import ConfigStore
from app.services.media import MediaController
from app.services.telemetry import TelemetryService
from app.services.events import EventLog

logging.basicConfig(level=logging.INFO)

def main():
    store = ConfigStore()
    telemetry = TelemetryService()
    media = MediaController(telemetry)

    # Let's overwrite both the transports and the groups to ensure we have a valid test matrix for 2 TX streams
    store.update({
        "encode_groups": [
            {
                "id": "g1", "name": "g1", "channel_count": 1, "enabled": True,
                "channels": [{"index": 1, "source_id": "dante-virtual-soundcard-x64-in-01"}]
            },
            {
                "id": "g2", "name": "g2", "channel_count": 1, "enabled": True,
                "channels": [{"index": 1, "source_id": "dante-virtual-soundcard-x64-in-02"}]
            }
        ],
        "srt_transports": [
            {
                "id": "tx-1", "name": "TX 1", "direction": "tx", "mode": "listener",
                "port": 12001, "latency_ms": 20, "encode_group_ids": ["g1"], "enabled": True
            },
            {
                "id": "tx-2", "name": "TX 2", "direction": "tx", "mode": "listener",
                "port": 12002, "latency_ms": 20, "encode_group_ids": ["g2"], "enabled": True
            }
        ]
    })

    print("Starting master spine...")
    # Make sure we have 2 capture channels available at minimum
    store.update({"audio": {"interface_name": "Dante Virtual Soundcard (x64)", "channel_count": 4}})
    media.start_spine(store.config)
    
    print("\nStarting TX leg 1...")
    media.start_srt_transport(store.config, "tx-1")
    time.sleep(1)

    print("\nStarting TX leg 2...")
    media.start_srt_transport(store.config, "tx-2")
    time.sleep(1)
    
    print("\nSUCCESS!")
    media.stop_all()

if __name__ == "__main__":
    main()
