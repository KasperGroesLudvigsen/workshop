# Improvements to implement

## 1 UI improvements

### Regarding the listing pop up box
This is about the box that appears when a listing on the map is clicked. 

Feedback to improve upon: 
- The text is overwhelming. All text is the same style which makes it tough to read. 
- New values to display: The pop up should display the following values: Price, house size, no. of rooms 
- Changes to existing displayed values: Add town and postal code alongside the street and street number in the top of the box
- Show a preview of the listing URL, ideally with a photo of the house, e.g. the first photo on the real estate agent website or a photo from boligsiden if retrievable
- If possible, the "pin" URL should open the address, not the geo coordinates in Google Maps. 
- replace apple maps link with link to the listing in Boligsiden so I can easily open it and save it in Boligsiden. 
- For each of the top 3 hangouts, add a link to their Google Maps entry. Example: For Fjordkroen 4733 Bækkeskov, this is what I wanna see: https://www.google.com/maps/place/Fjordkroen/@55.1677376,12.0277561,947m/data=!3m1!1e3!4m12!1m5!3m4!2zNTXCsDEwJzI5LjMiTiAxMsKwMDMnMTQuMiJF!8m2!3d55.17481!4d12.053944!3m5!1s0x4652c30039bcffc1:0xffb266d7f136a742!8m2!3d55.1682189!4d12.0328917!16s%2Fg%2F11y3nf51l0?entry=ttu&g_ep=EgoyMDI2MDkxNi4wIKXMDSoASAFQAw%3D%3D 

## 2 Other improvements
Analyze these errors and how to fix them. 

- According to the data, the listing Svinøvestervej 11, 4750 Lundby is 0.35 km from Cafe JaTak APS, but JaTak is actually in Vandværksvej 32 in Gelsted - far from Svinøvestervej. 
- Cafe JaTak, Vandværksvej 32 Gelsted, 4160 Gelsted, is permanently closed according to Google Maps. (https://cafejatak.dk/). Figure out if it is in fact closed. If it is, analyze the error and reason about how to avoid including businesses that are closed. 
- "FRIIS BYG & HAVE" (Rødkullevej 60, 4230 Rødkulle Huse) listed as close to the summerhouse on Kildevej 40 in Skælskør does not seem to be an actual cafe or restaurant.  
- In our data, the hangout "Havblik Agersø" is registered to Skolevangen 25, Magleby, but that's just where the business is registered. The actual restaurant is in Agersø Møllevej 9A, 4244 Agersø By.
- Café & Restaurant Mona, Adelgade 2, st, 4720 Præstø is permanently closed according to Google Maps. 

### 3 - oversvømmelse
For each listing, add a data point that indicates the risk of "oversvømmelse" from seawater on land. DinGeo has data on it like this: https://www.dingeo.dk/adresse/4243-rude/skolebakken-14/#oversvoem

Form your own ideas on how to implement this. My ideas are:
1) fetch the data from dinGeo if possible and allowed. Either via scrapes or API. We are using this for a hobby project - not commercially
2) find out how far above sea level listings are via højdekort or similar and define your own scoring mechanism. To sanity check the scoring mechanism, you can select a few test cases from the listing data set that you look up on dinGeo