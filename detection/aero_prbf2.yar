import "pe"

rule PRBF2_Aero_Internal_Cheat
{
    meta:
        description = "AERO internal cheat for Project Reality: BF2 (prbf2-internal.dll)"
        sha256 = "c8589be6c707a9998beb067de27f4ea03e20a7fcd3287053d23ba1fb8aba5831"
        date = "2026-10-05"
    strings:
        $id1 = "prbf2-internal.dll" ascii
        $id2 = "prbf2-internal.pdb" ascii
        $ipc1 = "Local\\PRBF2InternalStatus_%lu" ascii
        $ipc2 = "Local\\PRBF2InternalShow_%lu" ascii
        $ipc3 = "Local\\PRBF2InternalDisable_%lu" ascii
        $ui1 = "AERO##menu" ascii
        $ui2 = "play beyond." ascii
        $ui3 = "ENEMY MAY BE AIMING" ascii
        $ui4 = "Assist to visible target" ascii
        $ui5 = "Rage tracking" ascii
        $ui6 = "Flying grenade paths" ascii
        $ui7 = "aero.hlsl" ascii
        $log1 = "session-v20-%lu.log" ascii
        $log2 = "exceptions-v%u-%lu.log" ascii
        $err1 = "DirectX/recoil hooks could not be installed" ascii
        $err2 = "Could not initialize DirectInput hooks; no render hook installed" ascii
        $cfg = "glowChams" ascii
        $game = "RendDX9.dll" ascii
    condition:
        uint16(0) == 0x5A4D and
        ( any of ($id*) or any of ($ipc*) or 2 of ($err*)
          or (4 of ($ui*, $log*, $cfg) and $game) )
}

rule PRBF2_Generic_Internal_Cheat_Heuristic
{
    meta:
        description = "Generic: ImGui DX9 overlay DLL targeting PR:BF2 with aim/recoil/ESP config"
    strings:
        $g1 = "RendDX9.dll" ascii
        $g2 = "nlrif_c7nld_aimpoint" ascii
        $g3 = "frrif_famasaimpoint" ascii
        $i1 = "imgui_impl_dx9" ascii
        $c1 = "noRecoil" ascii
        $c2 = "aimFov" ascii
        $c3 = "aimStrength" ascii
        $c4 = "rageAim" ascii
        $c5 = "Enemy behind wall" ascii
    condition:
        uint16(0) == 0x5A4D and pe.is_dll() and $g1 and 1 of ($g2,$g3) and $i1 and 2 of ($c*)
}

rule PRBF2_Aero_Internal_Cheat_v2
{
    meta:
        description = "AERO for PR:BF2, newer build with aimbot, silent aim, artillery auto-fire and media widget"
        sha256 = "aa5bfaa28a09faa2c54a1ec7104ab375efe6deb5a33991679abc19077f3628f6"
        date = "2026-10-29"
    strings:
        $ps1 = "aero-media-v2.ps1" wide
        $ps2 = "-ExecutionPolicy Bypass -WindowStyle Hidden -File" wide
        $ua = "Aero overlay (lyrics lookup)" ascii
        $f1 = "Silent Aim" ascii
        $f2 = "Aim Through Walls" ascii
        $f3 = "Spread Force" ascii
        $k1 = "artyAutoFire" ascii
        $k2 = "aimMultipoint" ascii
        $k3 = "espSmartRadius" ascii
        $k4 = "mineEsp" ascii
        $id = "prbf2-internal.dll" ascii
    condition:
        uint16(0) == 0x5A4D and
        ( $ps1 or $ps2 or $ua or ($id and 2 of ($f*, $k*)) or 4 of ($f*, $k*) )
}
