# GMA DAVAO AMFM Caster - Production Deployment

## Recommended Windows deployment

1. Build on Windows using the same CPU architecture as the target machines.
2. Use the included `build_release.bat` in a clean checkout.
3. Test the generated executable on a clean Windows test machine before deployment.
4. Distribute the executable through a signed installer or a controlled application folder.
5. Do not place the application's writable configuration/log directory under `Program Files`.

The application stores writable configuration and logs under the user's local application-data directory, so normal users do not need administrator rights.

## Permissions

The application does **not** intentionally request administrator/UAC elevation.

Normal Windows user permissions are sufficient for:

- Saving application configuration and logs.
- Selecting audio input/output devices.
- Connecting to the configured WebSocket server.
- Using the current-user startup registry entry when Auto Start is enabled.

Windows may still require the user to grant microphone/privacy access to the packaged executable. Windows Firewall may also prompt the first time the executable makes a network connection.

## Auto-start

Auto Start uses `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`, which normally does not require administrator rights.

For a frozen PyInstaller executable, the startup command points directly to the executable. It does not pass the original `.py` filename.

## Configuration and logs

The app creates:

- `config_gma_caster.json`
- `caster_activity_log.txt`

in the per-user application-data directory. The config is written atomically so an interrupted write should not leave a half-written JSON file. A malformed existing config is preserved with a `.corrupt-YYYYMMDD-HHMMSS` suffix before defaults are used.

## Release testing checklist

- Launch as a standard Windows user.
- Start/stop AM independently.
- Start/stop FM independently.
- Test audio input at 44.1 kHz and 48 kHz.
- Test Opus/AAC/MP3/PCM formats supported by the server.
- Verify the server rejects unsupported codec configuration rather than receiving mislabeled PCM.
- Enable/disable local monitor.
- Change monitor device while active.
- Resize the main window, save the size, restart, and verify restoration.
- Lock/unlock the window size.
- Enable/disable Auto Start.
- Enable Minimize to Tray and quit from the tray menu.
- Disconnect the network while transmitting and reconnect.
- Remove/disable an audio device while the application is running.
- Run for several hours and watch CPU, memory, queue growth, and log size.
- Test a clean machine with no Python installation.

## Important audio-engine limitation

This release hardens the existing DSP implementation; it does not claim to be a studio-grade broadcast processor. The current DSP uses simple block processing. A future production audio-engine upgrade should add proper envelope followers, attack/release behavior, look-ahead limiting, crossover filters, true-peak measurement, and loudness metering.
