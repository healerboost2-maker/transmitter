; ============================================================
; GMA DAVAO AM-FM Caster - Inno Setup Installer
; Production installer
;
; Project layout:
;   transmitter\
;   ├── .venv\
;   └── amfm-caster\
;       ├── amfmCaster.py
;       ├── GMA_DAVAO_AMFM_Caster.spec
;       ├── am-fm_app.ico
;       ├── requirements-production.txt
;       ├── build_release.bat
;       ├── DEPLOYMENT.md
;       ├── GMA_DAVAO_AMFM_Caster.iss
;       └── dist\
;           └── GMA_DAVAO_AMFM_Caster.exe
;
; Build order:
;   1. build_release.bat
;   2. Open this .iss in Inno Setup
;   3. Build -> Compile
;   4. Installer is created in installer\
; ============================================================

#define AppName "GMA Davao AM-FM Caster"
#define AppVersion "2.2.3"
#define AppPublisher "GMA Davao"
#define AppExeName "GMA_DAVAO_AMFM_Caster.exe"
#define AppExePath "dist\" + AppExeName
#define AppIcon "am-fm_app.ico"
#define OutputDir "installer"
#define OutputBase "GMA_DAVAO_AMFM_Caster_Setup"

[Setup]
AppId={{8F7D3E5C-2F64-4D7D-9A91-6E8B8A9A2B31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=
AppSupportURL=
AppUpdatesURL=

DefaultDirName={autopf}\GMA Davao AM-FM Caster
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin

OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
SetupIconFile={#AppIcon}

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Do not install Python, .venv, source code, or development files.
; The PyInstaller executable is self-contained.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#AppExePath}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove only files created inside the application installation folder.
; User configuration/log files are intentionally kept in LocalAppData.

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;

  if not FileExists(ExpandConstant('{#AppExePath}')) then
  begin
    MsgBox(
      'The production executable was not found.' + #13#10 + #13#10 +
      'Expected:' + #13#10 +
      ExpandConstant('{#AppExePath}') + #13#10 + #13#10 +
      'Run build_release.bat first, then compile this installer again.',
      mbError,
      MB_OK
    );
    Result := False;
  end;
end;
