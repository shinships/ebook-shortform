import { staticFile } from "remotion";

export const demoProps = {
  title: "Slow Productivity",
  subtitle: "Cal Newport · The Lost Art of Accomplishment Without Burnout",
  audioSrc: staticFile("demo_audio.mp3"),
  coverSrc: staticFile("slow_productivity_cover_en.jpg"),
  accentColor: "#10b981", // Green matching Cal Newport's book typography
  subtitles: [
    { start: 0.0, end: 3.5, text: "Làm ít việc hơn để đạt được kết quả sâu sắc hơn." },
    { start: 3.5, end: 7.0, text: "Đó chính là triết lý cốt lõi của Slow Productivity." },
    { start: 7.0, end: 11.0, text: "Đừng để sự bận rộn giả tạo đánh cắp sự nghiệp của bạn." },
    { start: 11.0, end: 15.0, text: "Tập trung vào những dự án thật sự có giá trị lâu dài." }
  ]
};
