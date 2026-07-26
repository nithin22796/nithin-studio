export interface Service {
  name: string;
  description: string;
}

export const services: Service[] = [
  {
    name: "frame-extractor",
    description:
      "Upload a video, extract frames at an interval, download as a zip.",
  },
  {
    name: "file-manager",
    description:
      "Upload, organize into folders, preview, and download files.",
  },
  {
    name: "lora-trainer",
    description:
      "Train an SDXL LoRA from a set of images on a throwaway cloud GPU.",
  },
  {
    name: "image-upscaler",
    description:
      "Sharpen and upscale a photo 2x/4x, entirely locally — never smaller than the original.",
  },
  {
    name: "image-generator",
    description:
      "Generate SDXL images from a trained LoRA on a manually-controlled cloud GPU session.",
  },
];
