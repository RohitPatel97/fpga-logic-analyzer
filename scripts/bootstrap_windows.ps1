param([switch]$Renode)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsDirectory = Join-Path $projectRoot 'build\tools'
New-Item -ItemType Directory -Force $toolsDirectory | Out-Null

# Native MSYS2 builds; no system installation or PATH changes. Exact downloaded
# bytes are pinned so a changed upstream archive fails before extraction.
$packages = @(
    @('iverilog-1~13.0-2', 'e22c86dad1a0658d5f769362bfbec6533028c0d0bb63059b4fda0cc1084d1406'),
    @('libgcc-16.2.0-6', 'e0815432c4225892b0eef57055fe507fa0b9a036436f046605f6629696bd1b88'),
    @('libstdc++-16.2.0-6', '924b47e8c3e20b6448e29d3333acc638a4f4783c1c45230e09673532f9c4f198'),
    @('libwinpthread-git-12.0.0.r747.g1a99f8514-1', '38e0a33fc8b3b6b761d4958fa69afef6e0804e4ef7ea64018004da02f9b2294f'),
    @('bzip2-1.0.8-4', '123768f30ae14ba654a6feb70f8526146a331bc85f831a91a549ffb3f6cbffc7'),
    @('readline-8.3.003-1', 'b943d7e2a61ac6e0304eac11cdcee15370a8c2ccee5b84487f8ad6c1e103d66c'),
    @('ncurses-6.6-4', '7bf5159441d00262085d2619f6c26877ea7595d4dafc6840637c6c5fccda5cf5'),
    @('termcap-1.3.1-8', '205ece965d693274f200ff6c15bca8d29a093860951aeb4dbb9660817c255cc0'),
    @('zlib-1.3.2-2', '9e75842a070ba648e986e12424e1c92c9d7d77200e85f6a34eeb600819f2e694')
)

function Get-VerifiedArchive([string]$url, [string]$target, [string]$expectedHash) {
    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
    }
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($actual -ne $expectedHash) { throw "Checksum mismatch: $target. Remove this archive and retry." }
}

foreach ($package in $packages) {
    $filename = 'mingw-w64-x86_64-' + $package[0] + '-any.pkg.tar.zst'
    $archive = Join-Path $toolsDirectory $filename
    Get-VerifiedArchive ('https://repo.msys2.org/mingw/mingw64/' + $filename) $archive $package[1]
    & tar -xf $archive -C $toolsDirectory
    if ($LASTEXITCODE -ne 0) { throw "Extraction failed: $archive" }
}

if ($Renode) {
    $archive = Join-Path $toolsDirectory 'renode-1.17.0.windows-portable.zip'
    Get-VerifiedArchive 'https://github.com/renode/renode/releases/download/v1.17.0/renode-1.17.0.windows-portable.zip' $archive '18bcf145422039e8702c3e5f9864c7787561bfd5deb7a3ee6e168ecf19e2b9db'
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $toolsDirectory 'renode-1.17.0') -Force
}

Write-Host "Tools ready under $toolsDirectory. Run .\run.ps1 -Verify or .\run.ps1 -View."
