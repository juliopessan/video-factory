import { Composition } from "remotion";
import { LowerThird, lowerThirdSchema } from "./LowerThird";
import { Packshot, packshotSchema } from "./Packshot";

const FPS = 30;

/**
 * Duas composições, ambas com fundo transparente: o filme continua vindo do
 * modelo generativo e só a camada de marca é desenhada aqui, onde tipografia e
 * logo saem exatos.
 */
export const Root: React.FC = () => (
  <>
    <Composition
      id="LowerThird"
      component={LowerThird}
      durationInFrames={4 * FPS}
      fps={FPS}
      width={1920}
      height={1080}
      schema={lowerThirdSchema}
      defaultProps={{
        title: "Contoso",
        subtitle: "Fábrica de migração para Azure",
        accent: "#0f6cbd",
      }}
    />
    <Composition
      id="Packshot"
      component={Packshot}
      durationInFrames={5 * FPS}
      fps={FPS}
      width={1920}
      height={1080}
      schema={packshotSchema}
      defaultProps={{
        brand: "Contoso",
        claim: "Vamos migrar.",
        accent: "#0f6cbd",
      }}
    />
  </>
);
