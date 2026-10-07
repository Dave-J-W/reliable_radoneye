# Generates custom_components/reliable_radoneye/brand/icon.png (256x256) and icon@2x.png (512x512).
# Usage: powershell -ExecutionPolicy Bypass -File scripts/make_icon.ps1
Add-Type -AssemblyName System.Drawing

function New-Icon([int]$size, [string]$path) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)
    $s = $size / 256.0

    # Rounded square, calm teal
    $m = 12 * $s; $w = $size - 2 * $m; $r = 52 * $s; $d = 2 * $r
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $p.AddArc($m, $m, $d, $d, 180, 90)
    $p.AddArc($m + $w - $d, $m, $d, $d, 270, 90)
    $p.AddArc($m + $w - $d, $m + $w - $d, $d, $d, 0, 90)
    $p.AddArc($m, $m + $w - $d, $d, $d, 90, 90)
    $p.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 26, 140, 140))), $p)

    # White "Rn"
    $white = [System.Drawing.Brushes]::White
    $font = New-Object System.Drawing.Font("Segoe UI", (92 * $s), [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = [System.Drawing.StringAlignment]::Center
    $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
    $g.DrawString("Rn", $font, $white, (New-Object System.Drawing.RectangleF (0 + 8 * $s), (8 * $s), $size, $size), $fmt)

    # Three dots radiating up-right from the text (decay / counts), growing smaller
    $dots = @(@(176, 62, 12), @(198, 46, 9), @(216, 34, 6))
    foreach ($t in $dots) {
        $cx = $t[0] * $s; $cy = $t[1] * $s; $rr = $t[2] * $s
        $g.FillEllipse($white, $cx - $rr, $cy - $rr, 2 * $rr, 2 * $rr)
    }

    $dir = Split-Path $path -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
}

$out = Join-Path $PSScriptRoot "..\custom_components\reliable_radoneye\brand"
New-Icon 256 (Join-Path $out "icon.png")
New-Icon 512 (Join-Path $out "icon@2x.png")
