Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object Windows.Forms.Form
$form.Text = 'Windows MCP speed lab'
$form.StartPosition = 'Manual'
$form.Location = New-Object Drawing.Point(400,250)
$form.ClientSize = New-Object Drawing.Size(640,300)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.TopMost = $true
$fields = @()
for ($i=0; $i -lt 3; $i++) {
    $label = New-Object Windows.Forms.Label
    $label.Text = "Field $($i+1)"
    $label.Location = New-Object Drawing.Point(20,(25 + $i*60))
    $box = New-Object Windows.Forms.TextBox
    $box.Multiline = $true
    $box.Location = New-Object Drawing.Point(130,(20 + $i*60))
    $box.Size = New-Object Drawing.Size(460,30)
    $form.Controls.Add($label)
    $form.Controls.Add($box)
    $fields += $box
}
$status = New-Object Windows.Forms.Label
$status.Location = New-Object Drawing.Point(20,245)
$status.Size = New-Object Drawing.Size(590,35)
$status.Text = 'Ready'
$form.Controls.Add($status)
$button = New-Object Windows.Forms.Button
$button.Text = 'Verify locally'
$button.Location = New-Object Drawing.Point(130,205)
$button.Size = New-Object Drawing.Size(180,32)
$button.Add_Click({
    $values = @($fields | ForEach-Object { $_.Text })
    $passed = $values[0] -ceq 'alpha-123' -and $values[1] -ceq 'bravo+456' -and $values[2] -ceq 'charlie {789}'
    $status.Text = if ($passed) { 'PASS: all three values match' } else { 'FAIL: values differ' }
    @{ passed=$passed; values=$values; time=(Get-Date -Format o) } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'fixture-result.json') -Encoding UTF8
})
$form.Controls.Add($button)
[Windows.Forms.Application]::Run($form)
