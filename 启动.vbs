Set W = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
venv_py = folder & "\venv\Scripts\pythonw.exe"
If fso.FileExists(venv_py) Then
    py = venv_py
Else
    py = "pythonw.exe"
End If
script = folder & "\main.py"
W.Run """" & py & """ """ & script & """", 0, False
