; Kuro installer (Inno Setup 6). Build on Windows:
;   1. Install Inno Setup from jrsoftware.org
;   2. Double-click installer\build_installer.bat
; Produces dist\KuroSetup.exe — a per-user install, no admin needed.
;
; The installer puts Kuro's code in %LOCALAPPDATA%\Programs\Kuro and makes
; Start Menu / Desktop shortcuts to run.bat. The first run of run.bat creates
; the Python environment, and after that Kuro UPDATES HIMSELF from
; settings.update_url — you only ship this installer once per user.
; If Python 3 isn't on the PC, the installer downloads Python 3.12 from
; python.org and installs it per-user first (no admin, no questions).
;
; GitHub builds this automatically: pushing a tag like v1.0.1 runs
; .github/workflows/release.yml, which publishes KuroSetup.exe as a release.

#define AppVer GetEnv("KURO_VERSION") == "" ? "1.0.0" : GetEnv("KURO_VERSION")

; Builds Kuro by default. A custom character (a CHARACTER.json, see README)
; gets its own installer from this same script; nothing of it is in the repo:
;   ISCC /DCatName=Mochi /DCatDir=C:\path\to\mochi /DCatIcon=mochi.ico kuro.iss
; CatDir holds that character's CHARACTER.json and the character\ folder that
; scripts\make_icon.py writes (its icon and installer art).
#ifndef CatName
  #define CatName "Kuro"
#endif
#ifdef CatDir
  #ifndef CatIcon
    #define CatIcon "icon.ico"
  #endif
  #define CatArt CatDir + "\character\installer"
  #define CatIconSrc CatDir + "\character\" + CatIcon
  #define CatIconInstalled "{app}\character\" + CatIcon
#else
  #define CatArt "art"
  #define CatIconSrc "..\assets\icon.ico"
  #define CatIconInstalled "{app}\assets\icon.ico"
#endif

[Setup]
AppId={#CatName}DesktopCat
AppName={#CatName}
AppVersion={#AppVer}
AppPublisher=Drey
DefaultDirName={userpf}\{#CatName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename={#CatName}Setup
SetupIconFile={#CatIconSrc}
UninstallDisplayIcon={#CatIconInstalled}
UninstallDisplayName={#CatName}
WizardSmallImageFile={#CatArt}\small-55.bmp,{#CatArt}\small-64.bmp,{#CatArt}\small-83.bmp,{#CatArt}\small-110.bmp,{#CatArt}\small-138.bmp
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
Source: "..\scripts\slim.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
#ifdef CatDir
; the custom character: who it is, and its own art (updates never touch these)
Source: "{#CatDir}\CHARACTER.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#CatDir}\character\*"; DestDir: "{app}\character"; Flags: ignoreversion recursesubdirs; Excludes: "installer"
#endif

[Icons]
Name: "{userprograms}\{#CatName}"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; IconFilename: "{#CatIconInstalled}"
Name: "{userdesktop}\{#CatName}"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; IconFilename: "{#CatIconInstalled}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
Filename: "{app}\run.bat"; Description: "Start {#CatName} now (first start sets things up)"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; the Python environment and voices run.bat created
Type: filesandordirs; Name: "{localappdata}\{#CatName}"

[Code]
const
  PythonUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe';

var
  DownloadPage: TDownloadWizardPage;

function PythonFound(): Boolean;
var
  RC: Integer;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Python\Launcher\py.exe'))
    or FileExists(ExpandConstant('{win}\py.exe'));
  if not Result then
    Result := Exec(ExpandConstant('{cmd}'), '/c where py', '', SW_HIDE,
                   ewWaitUntilTerminated, RC) and (RC = 0);
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Getting Python',
    '{#CatName} runs on Python 3.12. It isn''t on this PC yet, so it''s being set up first.',
    @OnDownloadProgress);
end;

procedure InstallPython;
var
  RC: Integer;
  Ok: Boolean;
begin
  Ok := False;
  DownloadPage.Clear;
  DownloadPage.Add(PythonUrl, 'python-setup.exe', '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      DownloadPage.SetText('Installing Python 3.12...', 'This takes a minute.');
      DownloadPage.ProgressBar.Style := npbstMarquee;
      Ok := Exec(ExpandConstant('{tmp}\python-setup.exe'),
        '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0',
        '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 0);
    except
      Log('Python download failed: ' + GetExceptionMessage);
    end;
  finally
    DownloadPage.Hide;
  end;
  if not Ok then
    SuppressibleMsgBox('Python 3.12 could not be set up automatically.' + #13#10 +
      '{#CatName} will still be installed: get Python 3.12 from python.org' + #13#10 +
      '(tick "Add python.exe to PATH"), then start {#CatName}.', mbInformation, MB_OK, IDOK);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and not PythonFound() then
    InstallPython;
end;
