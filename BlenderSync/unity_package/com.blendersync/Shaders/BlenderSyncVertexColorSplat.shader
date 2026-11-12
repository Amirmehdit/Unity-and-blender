Shader "BlenderSync/VertexColorSplat"
{
    Properties
    {
        [Header(Layer 0 Base)]
        _Layer0Albedo("Layer 0 Albedo", 2D) = "white" {}
        _Layer0Color("Layer 0 Tint", Color) = (0.7, 0.65, 0.6, 1)
        _Layer0Smoothness("Layer 0 Smoothness", Range(0, 1)) = 0.3

        [Header(Layer 1 Red Weight)]
        _Layer1Albedo("Layer 1 Albedo (R)", 2D) = "white" {}
        _Layer1Color("Layer 1 Tint (R)", Color) = (0.35, 0.55, 0.25, 1)
        _Layer1Smoothness("Layer 1 Smoothness", Range(0, 1)) = 0.4

        [Header(Layer 2 Green Weight)]
        _Layer2Albedo("Layer 2 Albedo (G)", 2D) = "white" {}
        _Layer2Color("Layer 2 Tint (G)", Color) = (0.45, 0.42, 0.4, 1)
        _Layer2Smoothness("Layer 2 Smoothness", Range(0, 1)) = 0.2

        [Header(Layer 3 Blue Weight)]
        _Layer3Albedo("Layer 3 Albedo (B)", 2D) = "white" {}
        _Layer3Color("Layer 3 Tint (B)", Color) = (0.85, 0.88, 0.92, 1)
        _Layer3Smoothness("Layer 3 Smoothness", Range(0, 1)) = 0.7

        [Header(Triplanar and Blending)]
        _WorldScale("World Space Tiling Scale", Float) = 0.25
        _BlendSharpness("Height Blend Sharpness", Range(1.0, 32.0)) = 8.0
        [Toggle] _UseUV2Weights("Read Splat Weights from TEXCOORD2 Instead of COLOR", Float) = 0.0
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Opaque"
            "RenderPipeline" = "UniversalPipeline"
            "UniversalMaterialType" = "Lit"
        }
        LOD 300

        HLSLINCLUDE
        #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

        CBUFFER_START(UnityPerMaterial)
            half4 _Layer0Color;
            half4 _Layer1Color;
            half4 _Layer2Color;
            half4 _Layer3Color;
            half _Layer0Smoothness;
            half _Layer1Smoothness;
            half _Layer2Smoothness;
            half _Layer3Smoothness;
            float _WorldScale;
            half _BlendSharpness;
            half _UseUV2Weights;
        CBUFFER_END

        TEXTURE2D(_Layer0Albedo); SAMPLER(sampler_Layer0Albedo);
        TEXTURE2D(_Layer1Albedo); SAMPLER(sampler_Layer1Albedo);
        TEXTURE2D(_Layer2Albedo); SAMPLER(sampler_Layer2Albedo);
        TEXTURE2D(_Layer3Albedo); SAMPLER(sampler_Layer3Albedo);
        ENDHLSL

        Pass
        {
            Name "ForwardLit"
            Tags { "LightMode" = "UniversalForward" }

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex SplatVertex
            #pragma fragment SplatFragment
            #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE _MAIN_LIGHT_SHADOWS_SCREEN
            #pragma multi_compile _ _ADDITIONAL_LIGHTS
            #pragma multi_compile _ _FORWARD_PLUS
            #pragma multi_compile_fragment _ _SHADOWS_SOFT
            #pragma multi_compile_fog
            #pragma multi_compile_instancing
            #include_with_pragmas "Packages/com.unity.render-pipelines.universal/ShaderLibrary/DOTS.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float3 normalOS   : NORMAL;
                float4 color      : COLOR;
                float4 uv2        : TEXCOORD2;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float3 positionWS : TEXCOORD0;
                float3 normalWS   : TEXCOORD1;
                float4 weights    : TEXCOORD2;
                half   fogFactor  : TEXCOORD3;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            Varyings SplatVertex(Attributes input)
            {
                Varyings o = (Varyings)0;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_TRANSFER_INSTANCE_ID(input, o);

                VertexPositionInputs posInputs = GetVertexPositionInputs(input.positionOS.xyz);
                o.positionCS = posInputs.positionCS;
                o.positionWS = posInputs.positionWS;
                o.normalWS = TransformObjectToWorldNormal(input.normalOS);
                o.weights = lerp(input.color, input.uv2, _UseUV2Weights);
                o.fogFactor = ComputeFogFactor(posInputs.positionCS.z);
                return o;
            }

            half4 SampleTriplanar(TEXTURE2D_PARAM(tex, samp), float3 posWS, float3 normalWS, float scale)
            {
                float3 blend = pow(abs(normalWS), 4.0);
                blend /= max(dot(blend, float3(1, 1, 1)), 1e-4);
                half4 cx = SAMPLE_TEXTURE2D(tex, samp, posWS.zy * scale);
                half4 cy = SAMPLE_TEXTURE2D(tex, samp, posWS.xz * scale);
                half4 cz = SAMPLE_TEXTURE2D(tex, samp, posWS.xy * scale);
                return cx * blend.x + cy * blend.y + cz * blend.z;
            }

            half4 SplatFragment(Varyings input) : SV_Target
            {
                UNITY_SETUP_INSTANCE_ID(input);
                float3 nWS = NormalizeNormalPerPixel(input.normalWS);

                half4 c0 = SampleTriplanar(TEXTURE2D_ARGS(_Layer0Albedo, sampler_Layer0Albedo), input.positionWS, nWS, _WorldScale) * _Layer0Color;
                half4 c1 = SampleTriplanar(TEXTURE2D_ARGS(_Layer1Albedo, sampler_Layer1Albedo), input.positionWS, nWS, _WorldScale) * _Layer1Color;
                half4 c2 = SampleTriplanar(TEXTURE2D_ARGS(_Layer2Albedo, sampler_Layer2Albedo), input.positionWS, nWS, _WorldScale) * _Layer2Color;
                half4 c3 = SampleTriplanar(TEXTURE2D_ARGS(_Layer3Albedo, sampler_Layer3Albedo), input.positionWS, nWS, _WorldScale) * _Layer3Color;

                float3 w = saturate(input.weights.rgb);
                float w0 = saturate(1.0 - max(w.r, max(w.g, w.b)));
                float4 h = float4(w0 + c0.a, w.r + c1.a, w.g + c2.a, w.b + c3.a);
                h = pow(max(h, 0.001), _BlendSharpness);
                h /= dot(h, float4(1, 1, 1, 1));

                half3 albedo = c0.rgb * h.x + c1.rgb * h.y + c2.rgb * h.z + c3.rgb * h.w;
                half smoothness = _Layer0Smoothness * h.x + _Layer1Smoothness * h.y + _Layer2Smoothness * h.z + _Layer3Smoothness * h.w;

                InputData inputData = (InputData)0;
                inputData.positionWS = input.positionWS;
                inputData.normalWS = nWS;
                inputData.viewDirectionWS = GetWorldSpaceNormalizeViewDir(input.positionWS);
                inputData.shadowCoord = TransformWorldToShadowCoord(input.positionWS);
                inputData.fogCoord = InitializeInputDataFog(float4(input.positionWS, 1.0), input.fogFactor);
                inputData.bakedGI = SampleSH(nWS);

                SurfaceData surfaceData = (SurfaceData)0;
                surfaceData.albedo = albedo;
                surfaceData.alpha = 1.0h;
                surfaceData.smoothness = smoothness;
                surfaceData.occlusion = 1.0h;

                half4 col = UniversalFragmentPBR(inputData, surfaceData);
                col.rgb = MixFog(col.rgb, inputData.fogCoord);
                return col;
            }
            ENDHLSL
        }

        UsePass "BlenderSync/Lit/ShadowCaster"
        UsePass "BlenderSync/Lit/DepthOnly"
        UsePass "BlenderSync/Lit/DepthNormals"
    }
}
