$cli = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..\runtime\reference_runtime\cli.py')
)

& python $cli @args
$runtimeExitCode = $LASTEXITCODE
exit $runtimeExitCode
