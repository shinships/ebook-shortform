import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export interface SubtitleItem {
  text: string;
  start: number;
  end: number;
}

export interface PodcastShortProps {
  title: string;
  subtitle: string;
  audioSrc: string;
  coverSrc: string;
  subtitles: SubtitleItem[];
  accentColor?: string;
}

export const PodcastShort: React.FC<PodcastShortProps> = ({
  title,
  subtitle,
  audioSrc,
  coverSrc,
  subtitles,
  accentColor = "#10b981", // Emerald accent matching Cal Newport's cover
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  // Resolve staticFile if relative
  const resolvedCover = coverSrc
    ? coverSrc.startsWith("http") || coverSrc.startsWith("data:")
      ? coverSrc
      : staticFile(coverSrc)
    : "";

  const resolvedAudio = audioSrc
    ? audioSrc.startsWith("http") || audioSrc.startsWith("data:")
      ? audioSrc
      : staticFile(audioSrc)
    : "";

  // Active subtitle
  const currentSub = subtitles.find(
    (s) => currentTime >= s.start && currentTime <= s.end
  );

  // Smooth floating animation
  const floatAnim = Math.sin(frame / 22) * 6;

  // Scale spring on entry
  const cardScale = spring({
    frame,
    fps,
    config: { damping: 14, mass: 0.6 },
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#070b12",
        color: "#ffffff",
        fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "100px 60px",
        overflow: "hidden",
      }}
    >
      {/* Dynamic Background Glow matching accent color */}
      <div
        style={{
          position: "absolute",
          width: "900px",
          height: "900px",
          borderRadius: "50%",
          background: `radial-gradient(circle, ${accentColor}1c 0%, transparent 65%)`,
          top: "30%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          filter: "blur(70px)",
          pointerEvents: "none",
        }}
      />

      {/* Top Header Badge */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          background: "rgba(255, 255, 255, 0.06)",
          padding: "12px 28px",
          borderRadius: "100px",
          border: "1px solid rgba(255, 255, 255, 0.1)",
          backdropFilter: "blur(12px)",
        }}
      >
        <div
          style={{
            width: "10px",
            height: "10px",
            borderRadius: "50%",
            backgroundColor: accentColor,
            boxShadow: `0 0 14px ${accentColor}`,
          }}
        />
        <span
          style={{
            fontSize: "24px",
            fontWeight: 700,
            letterSpacing: "2px",
            textTransform: "uppercase",
            color: "rgba(255, 255, 255, 0.85)",
          }}
        >
          Ebook Podcast Digest
        </span>
      </div>

      {/* Center Ebook Cover Mockup & Info */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          transform: `scale(${cardScale}) translateY(${floatAnim}px)`,
          width: "100%",
        }}
      >
        {/* Realistic Book Mockup Aspect Ratio (3:4) */}
        <div
          style={{
            width: "520px",
            height: "720px",
            borderRadius: "16px 28px 28px 16px",
            overflow: "hidden",
            boxShadow:
              "-12px 20px 40px rgba(0, 0, 0, 0.7), 0 35px 70px rgba(0, 0, 0, 0.8), 0 0 50px rgba(16, 185, 129, 0.15)",
            border: "1px solid rgba(255, 255, 255, 0.15)",
            backgroundColor: "#161e2e",
            position: "relative",
          }}
        >
          {/* Subtle Book Spine Highlight on the left */}
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              width: "28px",
              background:
                "linear-gradient(to right, rgba(255, 255, 255, 0.15), rgba(0, 0, 0, 0.35) 70%, transparent)",
              zIndex: 10,
              pointerEvents: "none",
            }}
          />

          {resolvedCover && (
            <Img
              src={resolvedCover}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
          )}
        </div>

        {/* Title & Author */}
        <div style={{ marginTop: "40px", textAlign: "center", width: "100%", maxWidth: "900px" }}>
          <h1
            style={{
              fontSize: "44px",
              fontWeight: 800,
              lineHeight: 1.25,
              margin: 0,
              color: "#f8fafc",
            }}
          >
            {title}
          </h1>
          <p
            style={{
              fontSize: "26px",
              fontWeight: 500,
              color: "rgba(255, 255, 255, 0.6)",
              marginTop: "12px",
            }}
          >
            {subtitle}
          </p>
        </div>

        {/* Audio Waveform Bars */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "7px",
            height: "70px",
            marginTop: "28px",
          }}
        >
          {Array.from({ length: 32 }).map((_, i) => {
            const wave = Math.sin(frame * 0.22 + i * 0.35) * 0.5 + 0.5;
            const barHeight = 10 + wave * 52;
            return (
              <div
                key={i}
                style={{
                  width: "9px",
                  height: `${barHeight}px`,
                  borderRadius: "5px",
                  backgroundColor: i % 4 === 0 ? accentColor : "rgba(255, 255, 255, 0.35)",
                }}
              />
            );
          })}
        </div>
      </div>

      {/* Bottom Subtitles Box */}
      <div
        style={{
          width: "100%",
          maxWidth: "960px",
          minHeight: "200px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          backgroundColor: "rgba(15, 23, 42, 0.8)",
          borderRadius: "32px",
          padding: "32px 48px",
          border: "1px solid rgba(255, 255, 255, 0.08)",
          backdropFilter: "blur(20px)",
          boxShadow: "0 10px 30px rgba(0, 0, 0, 0.5)",
        }}
      >
        <span
          style={{
            fontSize: "36px",
            fontWeight: 700,
            lineHeight: 1.45,
            color: currentSub ? "#ffffff" : "rgba(255, 255, 255, 0.3)",
            textShadow: currentSub ? `0 0 24px ${accentColor}88` : "none",
          }}
        >
          {currentSub ? currentSub.text : "..."}
        </span>
      </div>

      {/* Audio Element */}
      {resolvedAudio && <Audio src={resolvedAudio} />}
    </AbsoluteFill>
  );
};
