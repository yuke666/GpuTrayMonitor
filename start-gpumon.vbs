Option Explicit
' Launch GPU Tray Monitor without any console window.
' Search order for pythonw.exe:
'   1. <project>\.venv\Scripts\pythonw.exe
'   2. %LOCALAPPDATA%\Programs\Python\Python3xx
'   3. %ProgramFiles%\Python3xx
'   4. pyw.exe (py launcher) on PATH
'   5. pythonw.exe on PATH
' Shows a dialog when none is found.

Const LAUNCH_ARGS = " --show"
Dim sh, fso, baseDir, scriptPath, pythonw, extraArgs

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = baseDir & "\gpumon.pyw"

If Not fso.FileExists(scriptPath) Then
    MsgBox "gpumon.pyw not found next to this file." & vbCrLf & vbCrLf & _
           "Keep this .vbs together with gpumon.pyw and the gpumon folder." & vbCrLf & vbCrLf & _
           baseDir, 16, "GPU Tray Monitor"
    WScript.Quit 1
End If

pythonw = FindPythonw()
If pythonw = "" Then
    MsgBox "Python 3.10+ was not found." & vbCrLf & vbCrLf & _
           "Option A: install Python from python.org and tick" & vbCrLf & _
           "          ""Add python.exe to PATH"" during setup." & vbCrLf & vbCrLf & _
           "Option B: use the portable build instead - the folder that" & vbCrLf & _
           "          contains the .exe plus an _internal folder." & vbCrLf & _
           "          It does not need Python at all.", 48, "GPU Tray Monitor"
    WScript.Quit 2
End If

' The py launcher needs "-3"; pythonw.exe takes the script directly.
If LCase(Right(pythonw, 8)) = "pyw.exe" Then
    extraArgs = " -3"
Else
    extraArgs = ""
End If

sh.CurrentDirectory = baseDir
sh.Run """" & pythonw & """" & extraArgs & " """ & scriptPath & """" & LAUNCH_ARGS, 0, False

' ---------------------------------------------------------------------------

Function FindPythonw()
    Dim candidate

    FindPythonw = ""

    candidate = baseDir & "\.venv\Scripts\pythonw.exe"
    If fso.FileExists(candidate) Then
        FindPythonw = candidate
        Exit Function
    End If

    candidate = NewestPythonIn(sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python")
    If candidate <> "" Then
        FindPythonw = candidate
        Exit Function
    End If

    candidate = NewestPythonIn(sh.ExpandEnvironmentStrings("%ProgramFiles%"))
    If candidate <> "" Then
        FindPythonw = candidate
        Exit Function
    End If

    candidate = FindOnPath("pyw.exe")
    If candidate <> "" Then
        FindPythonw = candidate
        Exit Function
    End If

    FindPythonw = FindOnPath("pythonw.exe")
End Function

Function NewestPythonIn(root)
    Dim folder, child, best, leaf

    NewestPythonIn = ""
    best = ""
    If root = "" Then Exit Function
    If Not fso.FolderExists(root) Then Exit Function

    Set folder = fso.GetFolder(root)
    For Each child In folder.SubFolders
        leaf = LCase(child.Name)
        If Left(leaf, 7) = "python3" Then
            If fso.FileExists(child.Path & "\pythonw.exe") Then
                If child.Name > best Then best = child.Name
            End If
        End If
    Next

    If best <> "" Then NewestPythonIn = root & "\" & best & "\pythonw.exe"
End Function

Function FindOnPath(exeName)
    Dim parts, i, entry, candidate

    FindOnPath = ""
    parts = Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
    For i = 0 To UBound(parts)
        entry = Trim(parts(i))
        If Len(entry) > 1 Then
            If Left(entry, 1) = """" Then entry = Mid(entry, 2)
            If Right(entry, 1) = """" Then entry = Left(entry, Len(entry) - 1)
            If Right(entry, 1) <> "\" Then entry = entry & "\"
            candidate = entry & exeName
            If fso.FileExists(candidate) Then
                FindOnPath = candidate
                Exit Function
            End If
        End If
    Next
End Function
