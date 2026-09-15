[Setup]
AppName=GMA DAVAO AMFM Caster
AppVersion=1.0
DefaultDirName={autopf}\GMA DAVAO AMFM Caster
DefaultGroupName=GMA DAVAO AMFM Caster
OutputDir=Output
OutputBaseFilename=AMFMCaster_Setup_v1.0
Compression=lzma
SolidCompression=yes
SetupIconFile=C:\Users\NEIL 4TH\Documents\code base\py\trx\transmitter\am-fm_app.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Main executable from PyInstaller dist folder
Source: "C:\Users\NEIL 4TH\Documents\code base\py\trx\transmitter\dist\amfmCaster\amfmCaster.exe"; DestDir: "{app}"; Flags: ignoreversion
; Supporting dependencies folder
Source: "C:\Users\NEIL 4TH\Documents\code base\py\trx\transmitter\dist\amfmCaster\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Icon file copied locally for shortcuts
Source: "C:\Users\NEIL 4TH\Documents\code base\py\trx\transmitter\am-fm_app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{group}\GMA DAVAO AMFM Caster"; Filename: "{app}\amfmCaster.exe"; IconFilename: "{app}\am-fm_app.ico"
Name: "{autodesktop}\GMA DAVAO AMFM Caster"; Filename: "{app}\amfmCaster.exe"; IconFilename: "{app}\am-fm_app.ico"; Tasks: desktopicon

[UninstallRun]
; Force stop any running instances of the app before uninstallation starts to release locked files
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im amfmCaster.exe"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
; Completely remove everything left in the application folder (including logs, configs, or leftover files)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\*.*"
Type: dirifempty; Name: "{app}"

[Run]
Filename: "{app}\amfmCaster.exe"; Description: "{cm:LaunchProgram,GMA DAVAO AMFM Caster}"; Flags: nowait postinstall skipifsilent