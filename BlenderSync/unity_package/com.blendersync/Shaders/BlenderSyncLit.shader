Shader "BlenderSync/Lit"
{
    Properties
    {
        [MainTexture] _BaseMap("Base Map", 2D) = "white" {}
        [MainColor] _BaseColor("Base Color", Color) = (1, 1, 1, 1)
        _Metallic("Metallic", Range(0.0, 1.0)) = 0.0
        _Smoothness("Smoothness", Range(0.0, 1.0)) = 0.5
        [NoScaleOffset] _MetallicRoughnessMap("Metallic (R) Roughness (G)", 2D) = "white" {}
        [Normal] [NoScaleOffset] _BumpMap("Normal Map", 2D) = "bump" {}
        _BumpScale("Normal Scale", Float) = 1.0
        [NoScaleOffset] _OcclusionMap("Occlusion", 2D) = "white" {}
        _OcclusionStrength("Occlusion Strength", Range(0.0, 1.0)) = 1.0
        [HDR] _EmissionColor("Emission Color", Color) = (0, 0, 0, 0)
        [NoScaleOffset] _EmissionMap("Emission Map", 2D) = "white" {}
        [Toggle] _UseVertexColor("Multiply by Linear Vertex Color", Float) = 0.0
        [Toggle(_ALPHATEST_ON)] _AlphaClip("Alpha Clip", Float) = 0.0
        _Cutoff("Alpha Cutoff", Range(0.0, 1.0)) = 0.5
        [Enum(UnityEngine.Rendering.CullMode)] _Cull("Cull Mode", Float) = 2.0
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Opaque"
            "RenderPipeline" = "UniversalPipeline"
            "UniversalMaterialType" = "Lit"
            "IgnoreProjector" = "True"
        }
        LOD 300

        HLSLINCLUDE
        #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

        CBUFFER_START(UnityPerMaterial)
            float4 _BaseMap_ST;
            half4 _BaseColor;
            half4 _EmissionColor;
            half _Metallic;
            half _Smoothness;
            half _BumpScale;
            half _OcclusionStrength;
            half _UseVertexColor;
            half _Cutoff;
            half _Cull;
        CBUFFER_END

        TEXTURE2D(_BaseMap);              SAMPLER(sampler_BaseMap);
        TEXTURE2D(_MetallicRoughnessMap); SAMPLER(sampler_MetallicRoughnessMap);
        TEXTURE2D(_BumpMap);              SAMPLER(sampler_BumpMap);
        TEXTURE2D(_OcclusionMap);         SAMPLER(sampler_OcclusionMap);
        TEXTURE2D(_EmissionMap);          SAMPLER(sampler_EmissionMap);
        ENDHLSL

        // ------------------------------------------------------------------
        // 1. ForwardLit Pass (Forward & Forward+ & GPU Resident Drawer)
        // ------------------------------------------------------------------
        Pass
        {
            Name "ForwardLit"
            Tags { "LightMode" = "UniversalForward" }
            Cull [_Cull]

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex LitPassVertex
            #pragma fragment LitPassFragment

            #pragma shader_feature_local _ALPHATEST_ON
            #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE _MAIN_LIGHT_SHADOWS_SCREEN

---
