$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$cpu = $null
$diskTemperature = $null
function Read-SmartTemperature([byte[]]$data) {
    if ($data.Length -ne 512) { return $null }
    foreach ($attribute in @(194, 190)) {
        for ($offset = 2; $offset -lt 362; $offset += 12) {
            if ($data[$offset] -eq $attribute -and $data[$offset + 5] -gt 0 -and $data[$offset + 5] -le 125) {
                return [double]$data[$offset + 5]
            }
        }
    }
    return $null
}
# CPU temperature is supplied separately by the PawnIO service.
try {
    # Follow the system partition to its disk; never assume that C: is PhysicalDrive0.
    $letter = $env:SystemDrive.TrimEnd(':')
    $disk = Get-Partition -DriveLetter $letter | Get-Disk
    if (@($disk).Count -eq 1) {
        $counter = $disk | Get-StorageReliabilityCounter
        if ($counter.Temperature -gt 0 -and $counter.Temperature -le 125) {
            $diskTemperature = [double]$counter.Temperature
        }
    }
} catch {}
if ($null -eq $diskTemperature -and $null -ne $disk -and @($disk).Count -eq 1) {
    try {
        $device = Get-CimInstance -ClassName Win32_DiskDrive -Filter "Index = $($disk.Number)" -OperationTimeoutSec 2
        if ($device.PNPDeviceID) {
            $pattern = '^' + [regex]::Escape($device.PNPDeviceID) + '(?:_\d+)?$'
            $smart = @(Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictData -OperationTimeoutSec 2 |
                Where-Object { $_.InstanceName -match $pattern })
            if ($smart.Count -eq 1) { $diskTemperature = Read-SmartTemperature $smart[0].VendorSpecific }
        }
    } catch {}
}
@{ cpu = $cpu; disk = $diskTemperature } | ConvertTo-Json -Compress
