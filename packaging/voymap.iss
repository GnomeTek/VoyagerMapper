; Inno Setup script for voymap. Expects the PyInstaller output in ..\dist\voymap.
; Build:  iscc /DAppVersion=0.1.0 packaging\voymap.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "voymap"
#define AppPublisher "voymap contributors"
#define AppURL "https://github.com/GnomeTek/VoyagerMapper"

[Setup]
AppId={{7E1F2C1A-6D2B-4B7E-9A47-VOYMAP000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=voymap-setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\voymap.exe
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add voymap-cli to the PATH (command-line use)"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "..\dist\voymap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\voymap"; Filename: "{app}\voymap.exe"
Name: "{group}\voymap README"; Filename: "{app}\_internal\README.md"
Name: "{group}\Uninstall voymap"; Filename: "{uninstallexe}"
Name: "{autodesktop}\voymap"; Filename: "{app}\voymap.exe"; Tasks: desktopicon

[Registry]
; user-scope PATH entry (PrivilegesRequired=lowest installs per-user by default)
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\voymap.exe"; Description: "{cm:LaunchProgram,voymap}"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;
