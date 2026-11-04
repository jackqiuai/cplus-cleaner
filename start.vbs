Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
exe = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "dist\CPlusCleaner.exe")
If fso.FileExists(exe) Then
  shell.Run Chr(34) & exe & Chr(34), 1, False
Else
  MsgBox "Build the Rust application with build.ps1 first.", 64, "CPlus Cleaner"
End If
