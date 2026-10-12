param([Parameter(Mandatory=$true)][string]$ImagePath,[string]$Language='en-US')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'})[0]
function Wait-WinRT($Operation,[Type]$ResultType) {
    $task=$asTask.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    $task.GetAwaiter().GetResult()
}
$file=Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync([IO.Path]::GetFullPath($ImagePath))) ([Windows.Storage.StorageFile])
$stream=Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder=Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Wait-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$lang=[Windows.Globalization.Language]::new($Language)
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if ($null -eq $engine) { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { throw 'Windows OCR language pack unavailable; install an OCR language pack or use Vision / imported OCR.' }
$result=Wait-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines=@()
foreach ($line in $result.Lines) {
    $words=@()
    foreach ($word in $line.Words) {
        $rect=$word.BoundingRect
        $words+=@{text=$word.Text;x=$rect.X;y=$rect.Y;width=$rect.Width;height=$rect.Height}
    }
    $lines+=@{text=$line.Text;words=$words}
}
@{text=$result.Text;lines=$lines;language=$engine.RecognizerLanguage.LanguageTag;width=$bitmap.PixelWidth;height=$bitmap.PixelHeight} | ConvertTo-Json -Depth 8 -Compress
$bitmap.Dispose()
$stream.Dispose()
