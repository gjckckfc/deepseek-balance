' 无黑窗启动 DeepSeek 余额悬浮窗（双击运行）
Option Explicit
Dim sh, fso, s, scriptDir, pyw, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

pyw = ""
On Error Resume Next
Set s = sh.Exec("where pythonw.exe")
If Not s.StdOut.AtEndOfStream Then pyw = s.StdOut.ReadLine()
On Error GoTo 0

If pyw = "" Then
    cmd = "pythonw.exe """ & scriptDir & "\deepseek_balance_widget.py"""
Else
    cmd = """" & pyw & """ """ & scriptDir & "\deepseek_balance_widget.py"""
End If
sh.Run cmd, 0, False
