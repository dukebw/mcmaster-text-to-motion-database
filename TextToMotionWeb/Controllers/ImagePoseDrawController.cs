using Microsoft.AspNetCore.Mvc;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using TextToMotionWeb.Data;
using TextToMotionWeb.Models;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Hosting;
using System.IO;
using System.Runtime.InteropServices;
using System;
using System.Net.Http;

namespace TextToMotionWeb.Controllers
{
    public class ImagePoseDrawController : Controller
    {
        private readonly ApplicationDbContext _context;
        private readonly IHostingEnvironment _environment;

        //XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        //******************NEWLY ADDED*******************************

        public IActionResult DataTable(){
            return View();
        }


        //XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX




        private async Task<bool> DoesImageExist(int id)
        {
            return await _context.PoseDrawnImages.AnyAsync(m => m.ID == id);
        }

        /*
         * We use dependency injection to set the database session context, and environment in the controller.
         * Input parameter environment is used to get the absolute path when saving images.
         */
        public ImagePoseDrawController(ApplicationDbContext context, IHostingEnvironment environment)
        {
            _context = context;
            _environment = environment;
        }

        /*
         * GET: /ImagePoseDraw/
         * The index page shows a list of the names and descriptions of the pose-drawn images in the database.
         */
        public async Task<IActionResult> Index()
        {
            return View(await _context.PoseDrawnImages.ToListAsync());
        }

        /*
         * GET: /ImagePoseDraw/Details/5
         * Returns a view showing the details for the image given by id, or a NotFound page.
         */
        public async Task<IActionResult> Details(int? id)
        {
            if (id == null)
            {
                return NotFound();
            }

            var image = await _context.PoseDrawnImages.SingleOrDefaultAsync(m => m.ID == id);
            if (image == null)
            {
                return NotFound();
            }

            return View(image);
        }

        // GET: /ImagePoseDraw/Create
        public IActionResult Create()
        {
            return View();
        }

        [DllImport("libestimate_pose_wrapper.so.1.0.1", EntryPoint = "estimate_pose_wrapper")]
        private static extern int
        estimate_pose_wrapper([MarshalAs(UnmanagedType.LPArray)] byte[] image, ref int size_bytes, int max_size_bytes);

        /*
         * POST: /ImagePoseDraw/Create
         * Inserts a new entry into the posed-image database.
         * The entry has a unique reference to the image's filename where it is stored on the filesystem.
         *
         * @param [in] posedImage The metadata for the to-be-posed image. This metadata will be stored in
         *  the database.
         * @param [in] image The image that is to have pose estimations drawn on it.
         *
         * @return Asynchronous task that will result in a view showing the Index, or a re-display
         * of the create form on error.
         */
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult>
        Create([Bind("ID,Name,Description")] PoseDrawnImage posedImage, IFormFile image)
        {
            if (!ModelState.IsValid)
            {
                return View(posedImage);
            }

            byte[] rawImage;
            bool urlSelected = (String.Compare(Request.Form["url-selected"], "1") == 0);
            if (urlSelected)
            {
                string imageUrl = Request.Form["image-url"];

                using (HttpClient httpClient = new HttpClient())
                {
                    rawImage = await httpClient.GetByteArrayAsync(imageUrl);
                }
            }
            else
            {
                if ((image == null) || (image.Length <= 0))
                {
                    return View(posedImage);
                }

                using (Stream imageStream = image.OpenReadStream())
                {
                    rawImage = new byte[imageStream.Length];
                    int numBytesRead = 0;
                    do
                    {
                        numBytesRead += imageStream.Read(rawImage,
                                                         numBytesRead,
                                                         (int)imageStream.Length - numBytesRead);
                    } while (numBytesRead < imageStream.Length);
                }
            }

            int bytesWritten = rawImage.Length;
            int status = estimate_pose_wrapper(rawImage, ref bytesWritten, rawImage.Length);
            if (bytesWritten > rawImage.Length)
            {
                Array.Resize(ref rawImage, bytesWritten);
                status = estimate_pose_wrapper(rawImage, ref bytesWritten, rawImage.Length);
            }

            if (status < 0)
            {
                return View(posedImage);
            }
            
            _context.Add(posedImage);
            await _context.SaveChangesAsync();

            var uploads = Path.Combine(_environment.WebRootPath, "uploads");
            string imageName = posedImage.ID.ToString() + ".png";
            using (var fileStream = new FileStream(Path.Combine(uploads, imageName), FileMode.Create))
            {
                await fileStream.WriteAsync(rawImage, 0, bytesWritten);
            }

            return RedirectToAction("Index");
        }

        // GET: /ImagePoseDraw/Edit/5
        public async Task<IActionResult> Edit(int? id)
        {
            return await Details(id);
        }

        /*
         * POST: ImagePoseDraw/Edit/5
         * Update the content of the pose-drawn image database entry.
         * TODO(brendan): create a way to also update the image on the filesystem.
         */
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> Edit(int id, [Bind("ID,Name,Description")] PoseDrawnImage image)
        {
            if (id != image.ID)
            {
                return NotFound();
            }

            if (ModelState.IsValid)
            {
                try
                {
                    _context.Update(image);
                    await _context.SaveChangesAsync();
                }
                catch (DbUpdateConcurrencyException)
                {
                    bool doesImageExist = await DoesImageExist(image.ID);
                    if (!doesImageExist)
                    {
                        return NotFound();
                    }
                    else
                    {
                        throw;
                    }
                }
                return RedirectToAction("Index");
            }
            return View(image);
        }

        // GET: /ImagePoseDraw/Delete/5
        public async Task<IActionResult> Delete(int? id)
        {
            return await Details(id);
        }

        /*
         * POST: /ImagePoseDraw/Delete/5
         * Since the GET and POST methods for Delete have the same function signature,
         *  we have renamed the POST method to "DeleteConfirmed".
         */
        [HttpPost, ActionName("Delete")]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> DeleteConfirmed(int id)
        {
            var image = await _context.PoseDrawnImages.SingleOrDefaultAsync(m => m.ID == id);
            _context.PoseDrawnImages.Remove(image);

            await _context.SaveChangesAsync();

            return RedirectToAction("Index");
        }
    }
}
