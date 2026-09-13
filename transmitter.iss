[Setup]
AppName=GMA DAVAO AM/FM Caster
AppVersion=1.0
AppPublisher=GMA Network - Davao

; Install application into Program Files
DefaultDirName={autopf}\GMA DAVAO AMFM Caster
DefaultGroupName=GMA DAVAO AMFM Caster

OutputDir=output
OutputBaseFilename=GMA_DAVAO_AMFM_Caster_Setup

Compression=lzma
SolidCompression=yes

PrivilegesRequired=admin
SetupIconFile=am-fm_app.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]

; ============================================================
; PYINSTALLER APPLICATION
; ============================================================
Source: "dist\transmitter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Desktop shortcut icon
Source: "dsktp_app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]

; ============================================================
; USER CONFIGURATION DIRECTORY
; ============================================================
; Creates:
; C:\Users\<username>\AppData\Roaming\GMA DAVAO AMFM Caster
;
; The application can write its configuration here.
; ============================================================
Name: "{userappdata}\GMA DAVAO AMFM Caster"

[Icons]

; Start Menu
Name: "{group}\GMA DAVAO AM/FM Caster"; \
    Filename: "{app}\transmitter.exe"; \
    IconFilename: "{app}\transmitter.exe"

; Uninstaller
Name: "{group}\Uninstall GMA DAVAO AM/FM Caster"; \
    Filename: "{uninstallexe}"

; Desktop
Name: "{autodesktop}\GMA DAVAO AM/FM Caster"; \
    Filename: "{app}\transmitter.exe"; \
    IconFilename: "{app}\dsktp_app.ico"; \
    Tasks: desktopicon

[Tasks]

Name: "desktopicon"; \
    Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; \
    Flags: unchecked

[Run]

; Launch application after installation
Filename: "{app}\transmitter.exe"; \
    Description: "{cm:LaunchProgram,GMA DAVAO AM/FM Caster}"; \
    Flags: nowait postinstall skipifsilent

[Code]

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  // Force-terminate transmitter.exe before uninstall/update
  Exec(
    'taskkill.exe',
    '/f /im transmitter.exe',
    '',
    0,
    ewWaitUntilTerminated,
    ResultCode
  );

  Result := True;
end;