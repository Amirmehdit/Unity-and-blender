Shader "BlenderSync/AttributeDebug"
{
    Properties
    {
        [KeywordEnum(COLOR, NORMAL, TANGENT, UV0, UV1, UV2, UV3, UV4, UV5, UV6, UV7)]
        _DebugChannel("Inspect Channel", Float) = 0
        _ComponentMask("Component Mask (RGBA)", Vector) = (1, 1, 1, 0)
        _ValueScale("Value Multiplier", Float) = 1.0
    }

    SubShader
    {
        Tags { "RenderType" = "Opaque" "RenderPipeline" = "UniversalPipeline" }
        LOD 100

        Pass
        {
            Name "DebugForward"
            Tags { "LightMode" = "UniversalForward" }

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #include_with_pragmas "Packages/com.unity.render-pipelines.universal/ShaderLibrary/DOTS.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            CBUFFER_START(UnityPerMaterial)
                float4 _ComponentMask;
                float _DebugChannel;
                float _ValueScale;
            CBUFFER_END

            struct Attributes
            {
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
                float4 tangentOS  : TANGENT;
                float4 color      : COLOR;
                float4 uv0        : TEXCOORD0;
                float4 uv1        : TEXCOORD1;
                float4 uv2        : TEXCOORD2;
                float4 uv3        : TEXCOORD3;
                float4 uv4        : TEXCOORD4;
                float4 uv5        : TEXCOORD5;
                float4 uv6        : TEXCOORD6;
                float4 uv7        : TEXCOORD7;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float4 inspected  : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            Varyings vert(Attributes v)
            {
                Varyings o = (Varyings)0;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_TRANSFER_INSTANCE_ID(v, o);
                o.positionCS = TransformObjectToHClip(v.positionOS.xyz);

                int mode = (int)round(_DebugChannel);
                float4 val = v.color;
                if (mode == 1) val = float4(v.normalOS * 0.5 + 0.5, 1.0);
                else if (mode == 2) val = float4(v.tangentOS.xyz * 0.5 + 0.5, v.tangentOS.w * 0.5 + 0.5);
                else if (mode == 3) val = v.uv0;
                else if (mode == 4) val = v.uv1;
                else if (mode == 5) val = v.uv2;
                else if (mode == 6) val = v.uv3;
                else if (mode == 7) val = v.uv4;
                else if (mode == 8) val = v.uv5;
                else if (mode == 9) val = v.uv6;
                else if (mode == 10) val = v.uv7;

                o.inspected = val * _ValueScale;
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                UNITY_SETUP_INSTANCE_ID(i);
                float4 masked = i.inspected * _ComponentMask;
                // If only a single component is selected in mask, display it as grayscale
                float maskSum = _ComponentMask.x + _ComponentMask.y + _ComponentMask.z + _ComponentMask.w;
                if (maskSum <= 1.01)
                {
                    float scalar = dot(i.inspected, _ComponentMask);
                    return half4(scalar, scalar, scalar, 1.0);
                }
                return half4(masked.rgb, 1.0);
            }
            ENDHLSL
        }
    }
}
