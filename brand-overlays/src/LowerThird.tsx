import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { z } from "zod";

export const lowerThirdSchema = z.object({
  title: z.string(),
  subtitle: z.string(),
  accent: z.string(),
});

export const LowerThird: React.FC<z.infer<typeof lowerThirdSchema>> = ({ title, subtitle, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 200 } });
  const exit = interpolate(frame, [durationInFrames - 12, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = enter * exit;
  const slide = interpolate(enter, [0, 1], [-60, 0]);

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent", justifyContent: "flex-end", padding: 96 }}>
      <div style={{ opacity, transform: `translateX(${slide}px)`, display: "flex", gap: 24 }}>
        <div style={{ width: 8, backgroundColor: accent }} />
        <div>
          <div style={{ fontFamily: "Helvetica, Arial, sans-serif", fontSize: 64, fontWeight: 700, color: "white", letterSpacing: -1 }}>
            {title}
          </div>
          <div style={{ fontFamily: "Helvetica, Arial, sans-serif", fontSize: 32, color: "rgba(255,255,255,0.85)", marginTop: 8 }}>
            {subtitle}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
