; Inno Setup script for Portage Horse Race.
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; Produces dist\installer\PortageHorseRace-Setup-<version>.exe

#define MyAppName "Portage Horse Race"
#define MyAppVersion "0.6.0"
#define MyAppPublisher "Portage Men's Open"
#define MyAppExeName "PortageHorseRace.exe"

[Setup]
; Stable AppId so future versions upgrade in place (data lives in %LOCALAPPDATA%, preserved).
AppId={{7F3C9A62-1D84-4E77-9B0E-2A1B3C4D5E6F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Portage Horse Race
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
; Per-user install => no administrator/UAC prompt for a non-technical recipient.
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=PortageHorseRace-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\PortageHorseRace\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
