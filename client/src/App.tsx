import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./Layout";
import { DashboardPage } from "./DashboardPage";
import { ActivityPage } from "./ActivityPage";
import { ServicesPage } from "./ServicesPage";
import { ServiceDetailPage } from "./ServiceDetailPage";
import { FrameExtractorApp } from "./apps/frame-extractor/FrameExtractorApp";
import { FileManagerApp } from "./apps/file-manager/FileManagerApp";
import { LoraTrainerApp } from "./apps/lora-trainer/LoraTrainerApp";
import { ImageUpscalerApp } from "./apps/image-upscaler/ImageUpscalerApp";
import { ImageGeneratorApp } from "./apps/image-generator/ImageGeneratorApp";
import "./App.css";

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="services" element={<ServicesPage />} />
        <Route path="services/frame-extractor" element={<FrameExtractorApp />} />
        <Route path="services/file-manager" element={<FileManagerApp />} />
        <Route path="services/lora-trainer" element={<LoraTrainerApp />} />
        <Route path="services/image-upscaler" element={<ImageUpscalerApp />} />
        <Route path="services/image-generator" element={<ImageGeneratorApp />} />
        <Route path="services/:name" element={<ServiceDetailPage />} />
      </Route>
    </Routes>
  );
}

export default App;
