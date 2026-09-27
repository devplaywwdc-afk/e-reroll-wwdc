__int64 __fastcall sub_7A402A8(
        __int64 result,
        __int64 a2,
        __int64 a3,
        __int64 a4,
        __int64 a5,
        __int64 a6,
        __int64 a7,
        __int64 a8,
        __int16 a9,
        __int64 a10,
        __int64 a11,
        __int64 a12)
{
  __int64 v12; // x19
  int v13; // w8
  __int64 v14; // x0
  __int64 v15; // x1
  __int64 v16; // x0
  char v17; // w0
  int v18; // w9
  int v19; // w8
  int v20; // w8
  char v21; // w0
  __int64 v22; // x0
  char v23; // w8
  int v24; // w8
  char v25; // w8
  _QWORD v26[4]; // [xsp+0h] [xbp-20h] BYREF

  v12 = result;
  v26[3] = *(_QWORD *)(_ReadStatusReg(TPIDR_EL0) + 40);
  while ( 2 )
  {
    v13 = *(_DWORD *)(v12 + 132);
    *(_BYTE *)(v12 + 128) = 0;
    switch ( v13 )
    {
      case 0:
        sub_7A8A560(result, a2, a3, a4, a5, a6, a7, a8);
        if ( *(_QWORD *)(v12 + 136) )
        {
          result = sub_7A3A310(v12);
          if ( *(_DWORD *)(v12 + 132) != 11 )
            *(_BYTE *)(v12 + 128) = 1;
          *(_DWORD *)(v12 + 132) = 11;
          if ( *(_BYTE *)(v12 + 128) )
            continue;
        }
        else
        {
          BYTE1(v26[1]) = 0;
          WORD1(v26[1]) = 0;
          HIDWORD(v26[1]) = 0;
          v26[2] = 0;
          LOBYTE(v26[0]) = 16;
          strcpy((char *)v26 + 1, "CmdLogi");
          result = sub_745817C(v12, v26);
          v25 = v26[0];
          *(_QWORD *)(v12 + 136) = result;
          if ( (v25 & 1) != 0 )
            result = sub_2EFA56C(v26[2]);
          if ( *(_DWORD *)(v12 + 132) != 1 )
            *(_BYTE *)(v12 + 128) = 1;
          *(_DWORD *)(v12 + 132) = 1;
          if ( *(_BYTE *)(v12 + 128) )
            continue;
        }
        return result;
      case 1:
        result = sub_7A40690(v12);
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 2:
        if ( *(_QWORD *)(v12 + 136) )
        {
          result = sub_7A3A310(v12);
          if ( *(_DWORD *)(v12 + 132) != 11 )
            *(_BYTE *)(v12 + 128) = 1;
          *(_DWORD *)(v12 + 132) = 11;
          if ( *(_BYTE *)(v12 + 128) )
            continue;
        }
        else
        {
          HIBYTE(v26[2]) = 0;
          strcpy((char *)v26, "*CmdGetUserInquiryInfo");
          result = sub_745817C(v12, v26);
          v23 = v26[0];
          *(_QWORD *)(v12 + 136) = result;
          if ( (v23 & 1) != 0 )
          {
            sub_2EFA56C(v26[2]);
            result = *(_QWORD *)(v12 + 136);
          }
          v24 = *(_DWORD *)(v12 + 132);
          if ( result )
          {
            if ( v24 != 3 )
              *(_BYTE *)(v12 + 128) = 1;
            *(_DWORD *)(v12 + 132) = 3;
            if ( *(_BYTE *)(v12 + 128) )
              continue;
          }
          else
          {
            if ( v24 != 11 )
              *(_BYTE *)(v12 + 128) = 1;
            *(_DWORD *)(v12 + 132) = 11;
            if ( *(_BYTE *)(v12 + 128) )
              continue;
          }
        }
        return result;
      case 3:
        result = sub_7A409B4(v12);
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 4:
        if ( *(_QWORD *)(v12 + 168) )
        {
          result = sub_7A3A310(v12);
          if ( *(_DWORD *)(v12 + 132) != 11 )
            *(_BYTE *)(v12 + 128) = 1;
          *(_DWORD *)(v12 + 132) = 11;
          if ( *(_BYTE *)(v12 + 128) )
            continue;
        }
        else
        {
          v21 = sub_7AD6704(result, a2, a3, a4, a5, a6, a7, a8);
          v22 = sub_7A426F4(v21 & 1);
          *(_QWORD *)(v12 + 168) = v22;
          result = sub_8145274(v12, v22);
          if ( *(_DWORD *)(v12 + 132) != 5 )
            *(_BYTE *)(v12 + 128) = 1;
          *(_DWORD *)(v12 + 132) = 5;
          if ( *(_BYTE *)(v12 + 128) )
            continue;
        }
        return result;
      case 5:
        v16 = *(_QWORD *)(v12 + 168);
        if ( !v16 )
        {
          result = sub_7A3A310(v12);
          if ( *(_DWORD *)(v12 + 132) != 11 )
            *(_BYTE *)(v12 + 128) = 1;
          *(_DWORD *)(v12 + 132) = 11;
          if ( *(_BYTE *)(v12 + 128) )
            continue;
          return result;
        }
        result = sub_7A42750(v16, a2, a3, a4, a5, a6, a7, a8);
        if ( (result & 1) == 0 )
          goto LABEL_44;
        v17 = sub_7A42760(*(_QWORD *)(v12 + 168));
        v18 = *(_DWORD *)(v12 + 132);
        if ( (v17 & 1) != 0 )
        {
          v19 = 11;
          if ( v18 == 11 )
            goto LABEL_70;
        }
        else
        {
          v19 = 10;
          if ( v18 == 10 )
            goto LABEL_70;
        }
        *(_BYTE *)(v12 + 128) = 1;
LABEL_70:
        result = *(_QWORD *)(v12 + 168);
        *(_DWORD *)(v12 + 132) = v19;
        if ( !result )
          goto LABEL_44;
        result = (*(__int64 (__fastcall **)(__int64))(*(_QWORD *)result + 40LL))(result);
        *(_QWORD *)(v12 + 168) = 0;
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 6:
        result = sub_807D0A8(*(unsigned int *)(v12 + 176));
        if ( (result & 1) == 0 )
          goto LABEL_44;
        v20 = *(_DWORD *)(v12 + 180);
        *(_DWORD *)(v12 + 176) = 0;
        if ( *(_DWORD *)(v12 + 132) != v20 )
          *(_BYTE *)(v12 + 128) = 1;
        *(_DWORD *)(v12 + 132) = v20;
        *(_DWORD *)(v12 + 180) = 11;
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 7:
        result = sub_7A40C58(v12);
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 8:
        if ( !*(_QWORD *)(v12 + 160) )
        {
          *(_DWORD *)(v12 + 132) = 11;
          *(_BYTE *)(v12 + 128) = 1;
          continue;
        }
        result = sub_7AE0218(*(_QWORD *)(v12 + 152) + 16LL, *(_QWORD *)(v12 + 152) + 40LL);
        if ( *(_DWORD *)(v12 + 132) != 9 )
          *(_BYTE *)(v12 + 128) = 1;
        *(_DWORD *)(v12 + 132) = 9;
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 9:
        result = sub_7A40DC0(v12);
        if ( *(_BYTE *)(v12 + 128) )
          continue;
        return result;
      case 10:
        v14 = v12;
        v15 = 1;
        goto LABEL_43;
      case 11:
        v14 = v12;
        v15 = 3;
LABEL_43:
        result = sub_7A3A31C(v14, v15);
LABEL_44:
        if ( !*(_BYTE *)(v12 + 128) )
          return result;
        continue;
      default:
        return result;
    }
  }
}
