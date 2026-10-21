/**
 * Representative photo per catalogue vehicle (`catalogueId` -> file in `public/assets/vehicles/`),
 * from `vehicle-images/catalogue.csv`. The photos are third-party images without a
 * redistribution licence, and are committed for the demo; when a
 * file is absent the card falls back to the generic car art. Never used to infer vehicle data.
 */
const VEHICLE_IMAGE_FILES: Readonly<Record<string, string>> = {
  'CAR-01': 'CAR-01_Kia-Picanto_2025.jpg',
  'CAR-02': 'CAR-02_Toyota-Yaris-Cross_2025.jpg',
  'CAR-03': 'CAR-03_BYD-Dolphin_2025.jpg',
  'CAR-04': 'CAR-04_Seat-Leon_2025.jpg',
  'CAR-05': 'CAR-05_Kia-Niro_2025.jpg',
  'CAR-06': 'CAR-06_MG-MG4_2025.jpg',
  'CAR-07': 'CAR-07_Toyota-Corolla_2026.png',
  'CAR-08': 'CAR-08_Hyundai-Kona-EV_2026.png',
  'CAR-09': 'CAR-09_Toyota-Corolla-Cross_2025.png',
  'CAR-10': 'CAR-10_BYD-Atto-3_2025.png',
  'CAR-11': 'CAR-11_Kia-EV3_2025.jpg',
  'CAR-12': 'CAR-12_Hyundai-Tucson_2025.jpg',
  'CAR-13': 'CAR-13_Mazda-3_2025.png',
  'CAR-14': 'CAR-14_Tesla-Model-3_2025.jpg',
  'CAR-15': 'CAR-15_Skoda-Octavia_2025.jpg',
  'CAR-16': 'CAR-16_Kia-Sportage-PHEV_2026.jpg',
  'CAR-17': 'CAR-17_BYD-Seal_2025.jpg',
  'CAR-18': 'CAR-18_Toyota-RAV4_2025.webp',
  'CAR-19': 'CAR-19_Kia-Carnival_2025.jpg',
  'CAR-20': 'CAR-20_Hyundai-Santa-Fe_2025.png',
};

export function vehicleImageUrl(catalogueId: string | null | undefined): string | null {
  const file = catalogueId ? VEHICLE_IMAGE_FILES[catalogueId] : undefined;
  return file ? `/assets/vehicles/${file}` : null;
}
