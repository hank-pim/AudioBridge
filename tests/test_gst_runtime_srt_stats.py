from app.services.gst_runtime import _largest_int, _parse_stats


def _rx_stats(bytes_received: int, bytes_received_total: int) -> dict[str, str]:
    text = (
        f"application/x-srt-statistics, "
        f"bytes-received=(guint64){bytes_received}, "
        f"bytes-received-total=(guint64){bytes_received_total};"
    )
    return _parse_stats(text)


def _tx_stats(bytes_sent: int, bytes_sent_total: int) -> dict[str, str]:
    text = (
        f"application/x-srt-statistics, "
        f"bytes-sent=(guint64){bytes_sent}, "
        f"bytes-sent-total=(guint64){bytes_sent_total};"
    )
    return _parse_stats(text)


def test_srt_stats_parser_prefers_live_byte_counters_for_rx() -> None:
    fields = _rx_stats(bytes_received=10_000_000, bytes_received_total=2_000)

    bytes_total = _largest_int(
        fields,
        "bytes-received", "bytes-sent",
        "bytes-received-total", "bytes-sent-total",
        "pkti-recv-bytes", "pkti-send-bytes",
    )

    assert bytes_total == 10_000_000


def test_srt_stats_parser_prefers_live_byte_counters_for_tx() -> None:
    fields = _tx_stats(bytes_sent=8_500_000, bytes_sent_total=1_500)

    bytes_total = _largest_int(
        fields,
        "bytes-received", "bytes-sent",
        "bytes-received-total", "bytes-sent-total",
        "pkti-recv-bytes", "pkti-send-bytes",
    )

    assert bytes_total == 8_500_000
