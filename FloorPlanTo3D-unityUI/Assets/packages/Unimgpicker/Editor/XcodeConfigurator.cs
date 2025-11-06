#if UNITY_IOS && UNITY_EDITOR
using UnityEditor;
using UnityEditor.Callbacks;
using UnityEditor.iOS.Xcode;
using System.IO;

public class XcodeConfigurator
{
    [PostProcessBuild]
    public static void OnPostprocessBuild(BuildTarget buildTarget, string pathToBuiltProject)
    {
        if (buildTarget == BuildTarget.iOS)
        {
            // Path to Info.plist
            string plistPath = Path.Combine(pathToBuiltProject, "Info.plist");

            // Read the existing Info.plist
            PlistDocument plist = new PlistDocument();
            plist.ReadFromFile(plistPath);

            // Root dictionary of plist
            PlistElementDict rootDict = plist.root;

            // Add or update usage descriptions
            rootDict.SetString("NSPhotoLibraryUsageDescription", "This app requires access to your photo library to select and upload images.");
            rootDict.SetString("NSCameraUsageDescription", "This app requires access to your camera to take photos.");

            // Write changes back to Info.plist
            File.WriteAllText(plistPath, plist.WriteToString());
        }
    }
}
#endif
