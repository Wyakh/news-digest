using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("News Digest")]
[assembly: AssemblyDescription("News Digest")]
[assembly: AssemblyProduct("News Digest")]
[assembly: AssemblyCompany("Wyakh")]
[assembly: AssemblyCopyright("Wyakh")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: ComVisible(false)]

// An unsigned, non-elevating launcher only: all application behavior stays in tray.py.
internal static class NewsDigestLauncher
{
    [STAThread]
    private static void Main()
    {
        try
        {
            string location = Assembly.GetExecutingAssembly().Location;
            string root = String.IsNullOrEmpty(location)
                ? AppDomain.CurrentDomain.BaseDirectory
                : Path.GetDirectoryName(location);
            root = Path.GetFullPath(root);

            string interpreter = Path.Combine(root, "venv", "Scripts", "pythonw.exe");
            string tray = Path.Combine(root, "tray.py");
            if (!File.Exists(interpreter))
                throw new FileNotFoundException("The News Digest Python interpreter is missing: " + interpreter);
            if (!File.Exists(tray))
                throw new FileNotFoundException("The News Digest tray script is missing: " + tray);

            ProcessStartInfo start = new ProcessStartInfo();
            // FileName is the absolute executable path, not a shell command; do not quote it.
            start.FileName = interpreter;
            // Windows filenames cannot contain quotes; the sole argument is this full file path.
            start.Arguments = "\"" + tray + "\"";
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            using (Process child = Process.Start(start))
            {
                if (child == null)
                    throw new InvalidOperationException("Windows did not start the News Digest tray process.");
            }
        }
        catch (Exception error)
        {
            // tray.py owns startup.log after it starts. Pre-start failures must remain visible.
            MessageBox.Show("News Digest could not start.\r\n\r\n" + error.Message,
                "News Digest", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
