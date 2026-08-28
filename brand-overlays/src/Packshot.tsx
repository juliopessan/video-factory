import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { z } from "zod";

export const packshotSchema = z.object({
  brand: z.string(),
  claim: z.string(),
  accent: z.string(),
});

export const Packshot: React.FC<z.infer<typeof packshotSchema>> = ({ brand, claim, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 200 } });
  const scale = interpolate(enter, [0, 1], [0.94, 1]);
  const rule = interpolate(frame, [12, 36], [0, 420], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent", alignItems: "center", justifyContent: "center" }}>
      <div style={{ opacity: enter, transform: `scale(${scale})`, textAlign: "center" }}>
        <div style={{ fontFamily: "Helvetica, Arial, sans-serif", fontSize: 120, fontWeight: 700, color: "white", letterSpacing: -3 }}>
          {brand}
        </div>
        <div style={{ height: 4, width: rule, backgroundColor: accent, margin: "28px auto" }} />
        <div style={{ fontFamily: "Helvetica, Arial, sans-serif", fontSize: 40, color: "rgba(255,255,255,0.9)" }}>
          {claim}
        </div>
      </div>
    </AbsoluteFill>
  );
};
