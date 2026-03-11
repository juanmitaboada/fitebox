[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · 【 **Streaming** 】 · [Recordings](recordings.md) · [Architecture](architecture.md) · [Troubleshooting](troubleshooting.md) · [API](api.md) · [Diagnostics](diagnostics.md)

## Live Streaming (Optional)

![Fitebox Live Streaming](img/fitebox_dashboard_streaming.jpeg)

FITEBOX can stream to YouTube, Twitch, or any RTMP server while simultaneously recording locally. This was one of the hardest parts to get right.

### Configuration

On the Dashboard, select a streaming destination (YouTube / Twitch / Custom) and enter your stream key. Hit Start Streaming after the recording is running.

### How it works

The streaming pipeline maintains a **single persistent RTMP connection** and feeds video segments sequentially:

```
  Phase 1 - WAITING ..... polls until recording starts
  Phase 2 - BUFFERING ... waits ~15s for MKV to accumulate data
  Phase 3 - INTRO ....... feeds the intro bumper
  Phase 4 - LIVE ........ feeds the growing MKV file (video: copy, audio: re-encode)
  Phase 5 - DRAINING .... recording stopped, drains to natural EOF
  Phase 6 - OUTRO ....... feeds the outro bumper
  Phase 7 - CLOSING ..... closes RTMP connection cleanly
```

The live phase uses **`-c:v copy`** for video - zero CPU cost. Only the audio is re-encoded to fix timestamp irregularities that would otherwise cause audible clicks on the stream (see Troubleshooting).

Timestamp continuity across phases is maintained with `-output_ts_offset` chaining on each feeder process. Without this, YouTube sees timestamp jumps and kills the stream.

### Why a single RTMP connection?

Multiple connections caused YouTube to interpret each segment as a separate stream. The audience would see "stream ended" and have to rejoin. One persistent TCP session through the entire broadcast solved this.

[← README](../README.md) · [Configuration](configuration.md) · [Recording](recording.md) · 【 **Streaming** 】 · [Recordings](recordings.md) · [Architecture](architecture.md) · [Troubleshooting](troubleshooting.md) · [API](api.md) · [Diagnostics](diagnostics.md)
