# Statement test: two memo invoices, ONE PayPal payment for both, then split it across the invoices.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File scripts\sandbox\statement_test.ps1
# Sandbox only. Prints no secrets.
$ErrorActionPreference = "Stop"
$e = Get-Content .env | ConvertFrom-StringData
$base = $e.PAYPAL_BASE_URL
if ($base -notlike "*sandbox*") { throw "Not sandbox - stopping." }
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("$($e.PAYPAL_CLIENT_ID):$($e.PAYPAL_CLIENT_SECRET)"))
$tok = (Invoke-RestMethod -Method Post -Uri "$base/v1/oauth2/token" -Headers @{Authorization="Basic $auth"} -Body "grant_type=client_credentials").access_token
$H = @{Authorization="Bearer $tok"; "Content-Type"="application/json"}
$buyer = "sb-j47tnw53803377@personal.example.com"
$stamp = Get-Date -Format "MMddHHmmss"

function New-Inv($num, $ref, $items) {
  $body = @{
    detail = @{ currency_code="USD"; invoice_number=$num; reference=$ref; payment_term=@{term_type="NET_30"} }
    invoicer = @{ email_address=$e.PAYPAL_BUSINESS_EMAIL }
    primary_recipients = @(@{ billing_info=@{ email_address=$buyer } })
    items = $items
    configuration = @{ partial_payment=@{ allow_partial_payment=$true; minimum_amount_due=@{currency_code="USD"; value="1.00"} } }
  } | ConvertTo-Json -Depth 10
  $r = Invoke-RestMethod -Method Post -Uri "$base/v2/invoicing/invoices" -Headers $H -Body $body
  $id = ($r.href -split "/")[-1]
  Invoke-RestMethod -Method Post -Uri "$base/v2/invoicing/invoices/$id/send" -Headers $H -Body '{"send_to_recipient":false}' | Out-Null
  return $id
}
function Item($name, $amt) { @{ name=$name; quantity="1"; unit_amount=@{currency_code="USD"; value=$amt} } }

Write-Host "`n[1] Creating 2 invoices from 2 memos (kept lines only)..."
$A = New-Inv "A-$stamp" "MEMO-201" @( (Item "Oval 1.20ct (memo M-201, kept)" "1200.00") )
$B = New-Inv "B-$stamp" "MEMO-202" @( (Item "Stud pair (memo M-202, kept)" "500.00"), (Item "Pendant (memo M-202, kept)" "300.00") )
Write-Host "    Invoice A: $A  (`$1,200)   Invoice B: $B  (`$800)"

Write-Host "`n[2] Creating ONE PayPal payment of `$1,500 for the statement..."
$order = @{
  intent = "CAPTURE"
  purchase_units = @(@{ reference_id="STMT-$stamp"; description="Statement: A-$stamp + B-$stamp"; amount=@{currency_code="USD"; value="1500.00"} })
  payment_source = @{ paypal = @{ experience_context = @{ return_url="https://example.com/return"; cancel_url="https://example.com/cancel"; user_action="PAY_NOW" } } }
} | ConvertTo-Json -Depth 10
$o = Invoke-RestMethod -Method Post -Uri "$base/v2/checkout/orders" -Headers $H -Body $order
$approve = ($o.links | Where-Object { $_.rel -eq "payer-action" -or $_.rel -eq "approve" } | Select-Object -First 1).href
Write-Host "    Open this link, log in with the SANDBOX buyer, approve:" -ForegroundColor Yellow
Write-Host "    $approve"
Write-Host "    (It will land on example.com afterwards - that's expected.)"
Read-Host "`nPress Enter after you approved"

$cap = Invoke-RestMethod -Method Post -Uri "$base/v2/checkout/orders/$($o.id)/capture" -Headers $H -Body "{}"
$capId = $cap.purchase_units[0].payments.captures[0].id
Write-Host "    Captured: $($cap.status)  capture id $capId"

Write-Host "`n[3] Splitting the `$1,500 across the invoices: A gets 1,200 (full), B gets 300 (partial)..."
$today = Get-Date -Format "yyyy-MM-dd"
foreach ($p in @(@($A,"1200.00"), @($B,"300.00"))) {
  $pay = @{ method="PAYPAL"; payment_date=$today; amount=@{currency_code="USD"; value=$p[1]}; note="Statement payment, PayPal capture $capId" } | ConvertTo-Json -Depth 5
  Invoke-RestMethod -Method Post -Uri "$base/v2/invoicing/invoices/$($p[0])/payments" -Headers $H -Body $pay | Out-Null
}

Write-Host "`n[4] Result:"
foreach ($id in @($A,$B)) {
  $inv = Invoke-RestMethod -Uri "$base/v2/invoicing/invoices/$id" -Headers $H
  Write-Host ("    {0}  status={1}  total={2}  due={3}" -f $inv.detail.invoice_number, $inv.status, $inv.amount.value, $inv.due_amount.value)
  if ($id -eq $B) { Write-Host "    Invoice B payer page (check if Venmo shows): $($inv.detail.metadata.recipient_view_url)" -ForegroundColor Yellow }
}
