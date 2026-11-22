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
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=VideoDownloader_Setup_{#AppVersion}
SetupIconFile=..\assets\icons\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\VideoDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; FFmpeg is NOT bundled (licence/size). To ship it, copy ffmpeg.exe and ffprobe.exe into dist\VideoDownloader first,
; and distribute them only in line with the licence of the build you use.

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
