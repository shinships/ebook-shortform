import React from "react";
import { Composition } from "remotion";
import { PodcastShort } from "./PodcastShort";
import { demoProps } from "./demoProps";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="PodcastShort"
        component={PodcastShort}
        durationInFrames={180 * 30} // Hỗ trợ video tối đa lên tới 3 phút (180s @ 30fps)
        fps={30}
        width={1080}
        height={1920}
        defaultProps={demoProps}
      />
    </>
  );
};

