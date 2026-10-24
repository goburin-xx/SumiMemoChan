
#define MyAppName "隅っこメモちゃん"
#define MyAppExeName "SumiMemoChan.exe"

[Setup]
AppId={{84192706-7ec7-4a8c-bc28-c63eb3f44b47}
AppName={#MyAppName}
AppVersion=1.0.0
DefaultDirName={userdocs}\SumiMemoChan_App
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#SourcePath}\_build\installer
OutputBaseFilename=SumiMemoChan_Setup
SetupIconFile={#SourcePath}\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "startmenu"; Description: "スタートメニューにショートカットを作成する"; GroupDescription: "ショートカットの作成:"
Name: "desktopicon"; Description: "デスクトップにショートカットを作成する"; GroupDescription: "ショートカットの作成:"; Flags: unchecked

[Files]
Source: "{#SourcePath}\_build\dist\SumiMemoChan\*"; DestDir: "{app}"; Excludes: "memo.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenu
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName}を起動する"; Flags: nowait postinstall skipifsilent
