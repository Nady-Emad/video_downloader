; Inno Setup script (https://jrsoftware.org/isinfo.php). Build with tools\build_installer.bat, which passes
; /DAppVersion=<version from app_info.py>. Requires the PyInstaller one-folder build in dist\VideoDownloader.
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define AppName "Video Downloader"
#define AppExe "VideoDownloader.exe"

[Setup]
AppId={{6F2C8E1A-3B5D-4C7E-9A41-5D0A7C3B2E19}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
AppCopyright=Copyright (C) {#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Start Menu folder page is skipped; the Start Menu shortcut is a checkbox task instead.
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=no
UsePreviousAppDir=yes
UninstallDisplayName={#AppName}
CloseApplications=yes
#if FileExists(SourcePath + "..\LICENSE.txt")
LicenseFile=..\LICENSE.txt
#endif
OutputDir=.
OutputBaseFilename=VideoDownloader_Setup
SetupIconFile=..\assets\icons\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Default: C:\Program Files\Video Downloader (asks for administrator). In the privilege dialog the user can pick
; "Install just for me", which installs per-user (no administrator needed); the folder can always be changed.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\VideoDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; FFmpeg is NOT bundled (licence/size). To ship it, copy ffmpeg.exe and ffprobe.exe into dist\VideoDownloader first,
; and distribute them only in line with the licence of the build you use.

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: startmenuicon
Name: "{autoprograms}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
{ Uninstall removes only the program files and shortcuts. Settings, history and logs live in
  %APPDATA%\Video Downloader and are removed only if the user explicitly agrees. Downloaded media is never touched. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#AppName}');
    if DirExists(DataDir) and (not UninstallSilent) then
      if MsgBox('Also delete your settings, download history and logs?' + #13#10 + DataDir + #13#10#13#10 +
                'Your downloaded videos and music are NOT deleted either way.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
