[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$outputDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'public\icons'
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

foreach ($size in @(16, 32, 48, 128)) {
    $bitmap = [System.Drawing.Bitmap]::new($size, $size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.Clear([System.Drawing.Color]::FromArgb(11, 118, 110))
        $penWidth = [Math]::Max(1.5, $size / 14)
        $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, $penWidth)
        try {
            $margin = $size * 0.22
            $graphics.DrawRectangle($pen, $margin, $margin, $size - (2 * $margin), $size - (2 * $margin))
            $graphics.DrawLine($pen, $size * 0.43, $margin, $size * 0.43, $size - $margin)
            $graphics.DrawLine($pen, $size * 0.43, $size * 0.5, $size - $margin, $size * 0.5)
        } finally {
            $pen.Dispose()
        }
        $path = Join-Path $outputDirectory "icon-$size.png"
        $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}
