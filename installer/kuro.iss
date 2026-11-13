; Kuro installer (Inno Setup 6). Build on Windows:
;   1. Install Inno Setup from jrsoftware.org
;   2. Double-click installer\build_installer.bat
; Produces dist\KuroSetup.exe — a per-user install, no admin needed.
;
; The installer puts Kuro's code in %LOCALAPPDATA%\Programs\Kuro and makes
; Start Menu / Desktop shortcuts to run.bat. The first run of run.bat creates
; the Python environment (setup.bat), and after that Kuro UPDATES HIMSELF from
; settings.update_url — you only ship this installer once per user.

#define AppVer GetEnv("KURO_VERSION") == "" ? "1.0.0" : GetEnv("KURO_VERSION")

[Setup]
AppId=KuroDesktopCat
AppName=Kuro
AppVersion={#AppVer}
AppPublisher=Drey
DefaultDirName={userpf}\Kuro
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=KuroSetup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\run.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\setup.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__"
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs
Source: "..\server\*"; DestDir: "{app}\server"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__"

[Icons]
Name: "{userprograms}\Kuro"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"
Name: "{userdesktop}\Kuro"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\run.bat"; Description: "Start Kuro now (first start sets things up)"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; the Python environment and voices run.bat created
Type: filesandordirs; Name: "{localappdata}\Kuro"
