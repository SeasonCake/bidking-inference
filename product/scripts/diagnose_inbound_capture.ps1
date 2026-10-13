# -*- coding: utf-8 -*-
# 入站抓包深度诊断 —— 专查"计算器已抓到出站包、但抓不到服务器回来(入站/REV)的包"这一类问题。
# 背景: Npcap 抓 tcp port 10000, 出站(本机->服务器)方向正常, 但入站(服务器->本机)帧数=0 ->
#        引擎拿不到对局状态、一直"等待状态帧 / rev_not_game_frame"。这不是版本问题(新旧同一套 Npcap),
#        是本机"谁把入站包藏起来了": NDIS 过滤驱动(Killer/SmartByte 等)/ VPN/代理 TUN 虚拟网卡 /
#        网卡接收侧 offload / WFP 拦截。本脚本只读探测这四类, 不改任何设置, 生成一份文本给作者。
#
# 用法: 把本文件 + 收集入站诊断.bat 放到计算器包根目录(和 BidKingLive.exe 同级), 双击 .bat。
#        建议: 先【进一局游戏走到出价/读牌那一步】再双击, 这样第 8 项能量到真实入站流量。
#        管理员运行更全(WFP 那项要管理员); 不是管理员也能跑, 缺的项会标注。
#
# 安全: 全部只读(Get-* / netsh show / 读注册表卸载表)。不改设置、不读密钥、不联网上传。

param([string]$PackageDir = "")   # 可选: 计算器包根目录(自解压 .bat 会把 %~dp0 传进来, 好定位第9项日志)
$ErrorActionPreference = 'Continue'
$sb = New-Object System.Text.StringBuilder
function Add-Line($t) { [void]$sb.AppendLine([string]$t) }
function Add-Sec($t)  { Add-Line ""; Add-Line ("=" * 62); Add-Line "  $t"; Add-Line ("=" * 62) }
function Try-Do($label, $block) {
  try { & $block } catch { Add-Line "  [$label 探测失败] $($_.Exception.Message)" }
}

$GameServerIp = "8.133.195.27"     # 排位更新【后】的新服 IP(端口 10000)
$OldServerIp  = "203.107.63.192"   # 排位更新【前】的旧服 IP —— 更新前群友能抓到的就是这个
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

# 查一个远端 IP 的出接口(走哪块网卡), 返回一行描述; 命中虚拟/VPN/TAP 会标注。
function Route-Of($ip) {
  try {
    $r = Find-NetRoute -RemoteIPAddress $ip -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $r) { return "    $ip -> (查不到路由)" }
    $ad = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue
    $ifn = if ($ad) { $ad.InterfaceDescription } else { "if#$($r.InterfaceIndex)" }
    $flag = ""
    if ("$ifn" -match 'TAP|TUN|Clash|WireGuard|OpenVPN|Wintun|VPN|Virtual|虚拟|Loopback') { $flag = "  ⚠走了虚拟/VPN网卡(入站会绕过物理网卡抓包)" }
    return "    $ip -> 出接口[$ifn]  本地源IP=$($r.IPAddress)$flag"
  } catch { return "    $ip -> (路由查询失败)" }
}

Add-Line "BidKing 入站抓包深度诊断"
Add-Line "生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "管理员运行: $isAdmin  (False 时第7项 WFP 会缺, 其余正常)"
Add-Line "(本文件用于排查, 请整份发给作者。全部只读, 不含密钥/账号。)"

# ---------- 1. 全部网卡(含虚拟/隐藏): 找 VPN/代理 TUN / Killer ----------
Add-Sec "1. 全部网络适配器(含虚拟/隐藏 → 找 VPN/代理TUN、Killer)"
Try-Do "网卡列表" {
  Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Sort-Object Status | ForEach-Object {
    $flag = ""
    $d = "$($_.InterfaceDescription) $($_.Name)"
    if ($d -match 'TAP|TUN|Clash|WireGuard|OpenVPN|Wintun|VPN|Proxy|虚拟|Virtual|Hyper-V|VMware|VirtualBox|Loopback') { $flag += " ⚠可能导走流量" }
    if ($d -match 'Killer|Rivet|SmartByte') { $flag += " ⚠Killer系(重点嫌疑)" }
    Add-Line ("  [{0,-14}] {1,-42} 速率={2}{3}" -f $_.Status, $_.InterfaceDescription, $_.LinkSpeed, $flag)
  }
  Add-Line "  -- 新旧游戏服务器各走哪块网卡(核心对比: 旧服能抓、新服抓不到 = 新服被分流) --"
  Add-Line "  [旧服·更新前能抓]"
  Add-Line (Route-Of $OldServerIp)
  Add-Line "  [新服·更新后抓不到]"
  Add-Line (Route-Of $GameServerIp)
  Add-Line "  [判读] 若新服走了虚拟/VPN网卡、或和旧服走的网卡不同 → 新服回包绕过了物理网卡, Npcap 抓不到 = 根因。"
}

# ---------- 1b. 游戏进程【当前实际连接】的服务器(确认这台现在连的是哪个服 + 是否连上) ----------
Add-Sec "1b. 游戏当前实际连接的服务器(BidKing.exe 的活动 TCP 连接)"
Try-Do "游戏连接" {
  $gp = Get-Process -Name BidKing -ErrorAction SilentlyContinue
  if (-not $gp) { Add-Line "  (未发现 BidKing.exe 在运行 —— 请先启动游戏、进一局再跑本诊断)" }
  else {
    $conns = Get-NetTCPConnection -OwningProcess $gp.Id -ErrorAction SilentlyContinue |
      Where-Object { $_.RemotePort -eq 10000 -or $_.State -eq 'Established' } |
      Select-Object -Unique RemoteAddress, RemotePort, State
    if ($conns) {
      Add-Line "  BidKing.exe 当前 TCP 连接(远端=游戏服务器):"
      foreach ($c in $conns) {
        Add-Line ("    -> {0}:{1}  [{2}]" -f $c.RemoteAddress, $c.RemotePort, $c.State)
        if ("$($c.RemotePort)" -eq "10000") { Add-Line (Route-Of "$($c.RemoteAddress)") }
      }
      Add-Line "  [判读] 上面 :10000 的远端 IP = 他这台现在连的游戏服; 看它的路由走的是不是物理网卡。"
    } else { Add-Line "  (BidKing.exe 在跑但没抓到 :10000 的连接 —— 可能在大厅未进对局, 请进一局再跑)" }
  }
}

# ---------- 2. NDIS 过滤驱动绑定(Killer/SmartByte/安全软件 的 LWF 在这里现形) ----------
Add-Sec "2. NDIS 过滤驱动绑定(第三方 LWF = 最常见的入站拦截元凶)"
Try-Do "过滤驱动绑定" {
  Add-Line "  -- 各网卡上已启用、且【非微软自带】的过滤组件(ComponentID 不以 ms_ 开头 = 第三方) --"
  # ArrayList 在管道子作用域里 .Add 是就地修改, 跨作用域可靠(普通 $x=$true 在 ForEach-Object 里只改子作用域)。
  $thirdParty = New-Object System.Collections.ArrayList
  Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | ForEach-Object {
    $ad = $_
    Get-NetAdapterBinding -Name $ad.Name -ErrorAction SilentlyContinue | Where-Object { $_.Enabled } | ForEach-Object {
      $cid = "$($_.ComponentID)"
      if ($cid -notlike 'ms_*') {
        [void]$thirdParty.Add("$($ad.Name)|$cid")
        Add-Line ("    网卡[{0}] 绑定: {1}  ({2})  ⚠第三方过滤" -f $ad.Name, $_.DisplayName, $cid)
      }
    }
  }
  if ($thirdParty.Count -eq 0) { Add-Line "    (未发现第三方过滤组件 —— 那入站拦截更可能来自 VPN虚拟网卡 或 网卡offload)" }
}

# ---------- 3. 已装的网络管理/代理/安全软件(Killer/SmartByte/Clash/VPN/杀软) ----------
Add-Sec "3. 已安装的网络/代理/安全软件(可能装了拦截驱动)"
Try-Do "已装软件扫描" {
  $keys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  $pat = 'Killer|Rivet|SmartByte|Clash|Shadowrocket|v2ray|Trojan|WireGuard|OpenVPN|加速|网游加速|UU|迅游|Proxy|代理|VPN|360安全|360杀毒|火绒|腾讯电脑管家|QQ电脑管家|Kaspersky|卡巴|Norton|诺顿|McAfee|ESET|Bitdefender|Avast|AVG'
  $hits = @()
  foreach ($k in $keys) {
    Get-ItemProperty $k -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match $pat } | ForEach-Object {
      $hits += ("    {0}  {1}" -f $_.DisplayName, $_.DisplayVersion)
    }
  }
  if ($hits) { $hits | Sort-Object -Unique | ForEach-Object { Add-Line $_ } }
  else { Add-Line "    (未在卸载表匹配到常见网络管理/代理/安全软件名)" }
}

# ---------- 4. 网卡接收侧 offload(LRO/RSC/接收校验和 会吞/坏入站包) ----------
Add-Sec "4. 网卡接收侧 offload(接收合并/校验和卸载 会让 Npcap 抓不到入站)"
Try-Do "offload" {
  $act = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Loopback' }
  foreach ($a in $act) {
    Add-Line "  -- 网卡: $($a.InterfaceDescription) --"
    Try-Do "Rsc" { Get-NetAdapterRsc -Name $a.Name -ErrorAction SilentlyContinue | ForEach-Object { Add-Line ("    RSC(接收合并) IPv4=$($_.IPv4Enabled) IPv6=$($_.IPv6Enabled)") } }
    Try-Do "Lso" { Get-NetAdapterLso -Name $a.Name -ErrorAction SilentlyContinue | ForEach-Object { Add-Line ("    LSO(大包卸载) IPv4=$($_.IPv4Enabled) IPv6=$($_.IPv6Enabled)") } }
    Try-Do "Checksum" { Get-NetAdapterChecksumOffload -Name $a.Name -ErrorAction SilentlyContinue | ForEach-Object { Add-Line ("    校验和卸载 TCP接收=$($_.TcpIPv4Receive)  UDP接收=$($_.UdpIPv4Receive)") } }
    Try-Do "高级属性" {
      Get-NetAdapterAdvancedProperty -Name $a.Name -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'Offload|卸载|Coalesc|合并|Large|RSC|Receive|接收|Checksum|校验' } |
        ForEach-Object { Add-Line ("    高级属性: {0} = {1}" -f $_.DisplayName, $_.DisplayValue) }
    }
  }
  Add-Line "  [全局RSC状态] $(netsh int tcp show global 2>$null | Select-String -Pattern 'Receive Segment Coalescing|接收段合并' | ForEach-Object { $_.ToString().Trim() })"
}

# ---------- 5. 运行中的网络相关进程/服务 ----------
Add-Sec "5. 运行中的网络/代理/安全 相关进程与服务"
Try-Do "进程" {
  $pat = 'killer|smartbyte|rivet|clash|v2ray|xray|trojan|wireguard|openvpn|proxy|uu|xunyou|360|huorong|qqpcmgr|kav|avp|norton|mcafee|eset|bdagent|avast'
  $p = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match $pat } | Select-Object -ExpandProperty ProcessName -Unique
  if ($p) { Add-Line "  相关进程: $($p -join ', ')" } else { Add-Line "  (无匹配进程)" }
  $s = Get-Service -ErrorAction SilentlyContinue | Where-Object { ($_.Name + $_.DisplayName) -match $pat -and $_.Status -eq 'Running' } | Select-Object -ExpandProperty DisplayName -Unique
  if ($s) { Add-Line "  运行中相关服务: $($s -join ', ')" } else { Add-Line "  (无匹配运行服务)" }
}

# ---------- 6. Npcap 版本/服务 ----------
Add-Sec "6. Npcap 版本 / 服务"
Try-Do "Npcap" {
  $svc = Get-Service -Name npcap -ErrorAction SilentlyContinue
  Add-Line "  服务: $(if($svc){$svc.Status}else{'未找到 ⚠'})"
  $ver = (Get-Item "$env:SystemRoot\System32\Npcap\wpcap.dll" -ErrorAction SilentlyContinue).VersionInfo.ProductVersion
  Add-Line "  wpcap.dll 版本: $ver"
}

# ---------- 7. WFP 第三方过滤器/callout(需管理员) ----------
Add-Sec "7. WFP 过滤器 / callout(第三方拦截入站 → 需管理员)"
if ($isAdmin) {
  Try-Do "WFP" {
    $tmp = Join-Path $env:TEMP "wfp_state_bidking.xml"
    netsh wfp show state file="$tmp" 2>$null | Out-Null
    if (Test-Path $tmp) {
      $x = Get-Content $tmp -Raw
      $providers = [regex]::Matches($x, '<name>([^<]*)</name>') | ForEach-Object { $_.Groups[1].Value } |
        Where-Object { $_ -match 'Killer|SmartByte|Rivet|Clash|VPN|Proxy|360|火绒|Huorong|Kaspersky|Norton|McAfee|ESET|Avast|Bitdefender|Filter|Callout' } |
        Sort-Object -Unique | Select-Object -First 30
      if ($providers) { Add-Line "  WFP 里出现的第三方/过滤相关名字:"; $providers | ForEach-Object { Add-Line "    $_" } }
      else { Add-Line "  (WFP 状态里未匹配到明显第三方过滤名)" }
      Remove-Item $tmp -ErrorAction SilentlyContinue
    }
  }
} else {
  Add-Line "  [跳过] 非管理员运行。要查这项: 右键脚本→以管理员身份用 PowerShell 运行, 或右键 .bat→以管理员身份运行。"
}

# ---------- 8. 决定性测试: 入站包到底到没到本机(网卡RX增长) vs 计算器抓包(REV=0) ----------
Add-Sec "8. 决定性测试: 入站包有没有到本机(网卡接收计数) —— 请先进一局游戏!"
Try-Do "入站计数" {
  $act = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Loopback' } | Select-Object -First 1
  if ($act) {
    $s1 = Get-NetAdapterStatistics -Name $act.Name -ErrorAction SilentlyContinue
    Add-Line "  网卡 [$($act.InterfaceDescription)] 采样15秒(这15秒请停在游戏对局里/正常联网)..."
    Start-Sleep -Seconds 15
    $s2 = Get-NetAdapterStatistics -Name $act.Name -ErrorAction SilentlyContinue
    $rx = $s2.ReceivedUnicastPackets - $s1.ReceivedUnicastPackets
    $tx = $s2.SentUnicastPackets - $s1.SentUnicastPackets
    Add-Line "  15秒内: 接收(入站)包 +$rx   发送(出站)包 +$tx"
    Add-Line "  [判读] 入站 +$rx 明显>0 = 服务器的包【确实到了本机网卡】。若此时计算器仍抓不到入站(下面第9项 REV=0)"
    Add-Line "         → 说明入站包在【网卡到 Npcap 之间被拦/吞了】(NDIS过滤/offload/WFP), 锁定第1-4/7项的嫌疑。"
    Add-Line "         若入站 +$rx ≈ 0 = 入站根本没走这块网卡 → 多半走了别的网卡(VPN/代理TUN, 见第1项)。"
  }
}

# ---------- 9. 计算器自己的抓包状态(REV/入站帧数) ----------
Add-Sec "9. 计算器抓包状态(capture_source_status.json + monitor 日志方向)"
Try-Do "抓包状态" {
  # 自动找包根: 优先 .bat 传进来的包目录 $PackageDir, 再脚本所在目录/其上级
  $here = Split-Path -Parent $MyInvocation.MyCommand.Path
  $roots = @()
  if ($PackageDir) { $roots += @("$PackageDir\data\logs\live", "$PackageDir\..\data\logs\live") }
  $roots += @("$here\data\logs\live", "$here\..\data\logs\live", "$here")
  $cand = $roots | Where-Object { Test-Path "$_\capture_source_status.json" } | Select-Object -First 1
  if ($cand) {
    $j = Get-Content "$cand\capture_source_status.json" -Raw | ConvertFrom-Json
    Add-Line "  source=$($j.source)  active_session=$($j.active_session_id)  active_flows=$($j.active_flows)"
    Add-Line "  accepted_frames=$($j.accepted_frames)  sniffed=$($j.sniffed_packets)  ignored=$($j.ignored_frames)  kernel_recv=$($j.kernel_recv)  kernel_drop=$($j.kernel_drop)"
    Add-Line "  ignored_reasons=$($j.ignored_reasons | ConvertTo-Json -Compress)"
    $dirs = ($j.ignored_samples | ForEach-Object { $_.direction } | Sort-Object -Unique) -join ','
    Add-Line "  被忽略样本方向=$dirs   [若全是 SEND 且从没 REV = 入站帧从没进抓包队列, 与第8项对照定位]"
    $mo = "$cand\monitor.stdout.log"
    if (Test-Path $mo) {
      $revCount = (Select-String -Path $mo -Pattern '"?REV"?' -ErrorAction SilentlyContinue | Measure-Object).Count
      Add-Line "  monitor.stdout.log 里 REV 出现次数=$revCount   [0 = 全程零入站帧]"
    }
  } else {
    Add-Line "  (没找到 capture_source_status.json —— 请把本脚本放到计算器包根目录, 且先开过一次计算器+进一局)"
  }
}

# ---------- 写出(优先桌面, 群友最好找) ----------
$name = "BidKing入站诊断_{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss')
$out = $null
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), $PackageDir, (Split-Path -Parent $MyInvocation.MyCommand.Path))) {
  if (-not $dir) { continue }
  try { $p = Join-Path $dir $name; [System.IO.File]::WriteAllText($p, $sb.ToString(), (New-Object System.Text.UTF8Encoding($true))); $out = $p; break } catch {}
}
Write-Host ""
Write-Host "==== 诊断完成 ====" -ForegroundColor Green
Write-Host "报告已生成: $out"
Write-Host "请把这个 txt 整份发给作者(在桌面上)。"
Write-Host ""
