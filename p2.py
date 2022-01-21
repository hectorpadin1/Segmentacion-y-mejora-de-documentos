#!/usr/bin/env python3

import cv2 as cv
import imutils
import numpy as np
from imutils.perspective import four_point_transform
from skimage.filters import threshold_local
import os
import concurrent.futures


class DocScanner():


    def __init__(self) -> None:
        self.__name = None
        self.__show = False        


    def __showImage(self, name, img):
        if self.__show:
            cv.imshow(name, imutils.resize(img, height = 800))
            cv.waitKey(0)
            cv.destroyAllWindows()
    

    def __dominantColors(self, img):

        data = np.reshape(img, (-1,3))
        data = np.float32(data)
        
        criteria = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        flags = cv.KMEANS_RANDOM_CENTERS
        _,_,centers = cv.kmeans(data,1,None,criteria,10,flags)
        
        return centers


    def __extractMarks(self, img):
        
        # Convertimos la imagen a HSV y calculamos su color dominante
        img_hsv = cv.cvtColor(img, cv.COLOR_BGR2HSV)
        color = self.__dominantColors(img)[0].astype(np.int32)

        # Calculamos las máscaras para el color rojo y azul
        lower_red = np.array([0,50,30])
        upper_red = np.array([10,255,255])
        mask0 = cv.inRange(img_hsv, lower_red, upper_red)
        
        lower_red = np.array([170,50,30])
        upper_red = np.array([180,255,255])
        mask1 = cv.inRange(img_hsv, lower_red, upper_red)
        
        lower_blue = np.array([110,70,50])
        upper_blue = np.array([130,255,255])
        mask2 = cv.inRange(img_hsv, lower_blue, upper_blue)
        
        # Juntamos y dilatamos las máscaras para obtener un mayor grosor y poder
        # eliminar más marcas de bolígrafo
        mask = cv.dilate(mask0+mask1, np.ones((7, 7), np.uint8))+cv.dilate(mask2, np.ones((11, 11), np.uint8))
        self.__showImage("Deteccion de marcas: " +self.__name, mask)
        copy = img.copy()
        copy[np.where(mask==0)] = 0
        
        # Reemplazamos los colores sustraídos con el color dominante, el color del folio
        result = img-copy
        result[np.where((result==[0,0,0]).all(axis=2))] = color
        result = cv.GaussianBlur(result, (5,5), 0)

        return result


    def __extractText(self, gray):

        # Definimos el rango de negro sobre el nivel de gris
        dark_rng = np.array([0])
        light_rng = np.array([50])
        mask = cv.inRange(gray, dark_rng, light_rng)
        mask = cv.erode(mask, (5,5))
        self.__showImage("Texto segmentado: " + self.__name, mask)
        
        # Creamos un fondo blanco, ponemos a cero el texto segmentado y los juntamos
        x, y = gray.shape
        background = 255 * np.ones(shape=[x, y], dtype=np.uint8)
        detected_text = background - mask
        _, result = cv.threshold(detected_text, 50, 255, cv.THRESH_TOZERO)
        
        return result


    def __findContours(self, img):

        # Eliminación de ruido utilizando 
        sigma = np.std(img)
        blurred = cv.fastNlMeansDenoisingColored(img, None, 20, 7, 21)
        image_sharp = cv.addWeighted(img.copy(), 1.3, blurred, -1, 0)
        gray = cv.fastNlMeansDenoising(cv.cvtColor(image_sharp, cv.COLOR_BGR2GRAY), None, 20, 7, 7)

        # Detección de bordes de los documentos
        edged = cv.Canny(gray, sigma, sigma*2)
        self.__showImage("Operador Canny: " + self.__name, edged)

        # Buscar contornos en la imagen
        c = cv.findContours(cv.dilate(edged.copy(), np.ones(shape=(3,3))), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_TC89_KCOS)
        # Compatibilidad con versiones de OpenCV anteriores
        c = imutils.grab_contours(c)

        return sorted(c, key = cv.contourArea, reverse = True)[:5]


    def __filterContours(self, contours, shape):

        screenCnt = None

        # Entre los contornos encontrados, buscamos contornos con cuatro vértices
        # que representen una especie de polígono, ya que estamos buscando una 
        # forma poligonal
        for c in contours:
            per = cv.arcLength(c, True)
            approx = cv.approxPolyDP(c, 0.02*per, True)
            if len(approx) == 4:
                screenCnt = approx
                pass

        if (contours!=[]):
            # No se han detectado contornos válidos, por lo tanto tendremos que analizar más en profundidad
            # los que si hemos detectado
            if (screenCnt is None) or (cv.contourArea(screenCnt) < 20000):
                
                rect = cv.minAreaRect(np.array(max(contours, key = cv.contourArea), dtype=np.int32))
                points = cv.boxPoints(rect)
                fitted_contour = np.int64(points)
                screenCnt = fitted_contour

        # Si no encontramos ningún contonrno válido, recortamos parte de la imagen
        # y procedemos (puede que los bordes del folio estén fuera de la imagen)
        if (screenCnt is None) or (cv.contourArea(screenCnt) < 50000.0):
            i, j = shape
            screenCnt = np.array([[j*0.15, i*0.15], [j*0.15, i*0.85], [j*0.85, i*0.15], [j*0.85, i*0.85]], dtype=np.int32)
        
        return screenCnt


    def __docThreshold(self, img):
        
        # Aplicamos un thresholding local a la imagen, el valor del threshold será 
        # el valor de la media ponderada de cada vecindario, poderada por una 
        # distribución gausiana
        gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
        T = threshold_local(gray, 21, offset = 5, method = "gaussian")
        th_img = (gray > T).astype("uint8") * 255

        # Por último, aplicamos una operación cierre, lo que nos permite obtener 
        # unas letras más legibles, sin distorsionar mucho la forma
        kernel = cv.getStructuringElement(cv.MORPH_CROSS, (3,3))
        return cv.morphologyEx(th_img.copy(), cv.MORPH_CLOSE, kernel)


    def scanDocument(self, path_image, show=False):

        self.__show = show        
        self.__name = path_image
        
        # Abrimos la imagen y la comprimimos
        image = cv.imread("Material Documentos/"+path_image)
        ratio = image.shape[0]/500.0
        initial = image.copy()
        image = imutils.resize(image, height = 500)
        self.__showImage(self.__name, image)

        # Devuelve todos los contornos encontrados aplicando Canny, a la imagen
        # previa se le aplica una técnica de Unsharp Masking para realzar los bordes
        # y un suavizado. Una vez obtenemos los bordes, sacamos los contornos
        # aplicando el algoritmo Suzuki85.
        contours = self.__findContours(image.copy())
        img_contours = image.copy()
        cv.drawContours(img_contours, contours, -1, (0, 255, 0), 2)
        self.__showImage("Contornos extraidos: " + self.__name, img_contours)

        # Analizamos los contornos obtenidos, ya que puede que no hayamos encontrado 
        # ningún contorno válido, esta función se encarga de analizar los contornos 
        # encontrados para estimar la localización de las esquinas del documento
        best_contour = self.__filterContours(contours, image.shape[:2])

        # Reajustamos el documento para que quede en vertical
        warped = (four_point_transform(initial, best_contour.reshape(4, 2) * ratio))
        self.__showImage("Documento reestructurado: " + self.__name, warped)
        
        # Extraemos los canales que corresponden a las marcas de bolígrafo (rojo y azul)
        warped = self.__extractMarks(warped)
        self.__showImage("Imagen sin marcas: " + self.__name, warped)

        # Aplicamos un threshold a la imagen
        thresholded = self.__docThreshold(warped)
        self.__showImage("Threshold aplicado: " + self.__name, thresholded)

        # Extraemos el texto y lo ponemos sobre un fondo blanco
        final = self.__extractText(thresholded)
        self.__showImage("Imagen final: " + self.__name, final)

        cv.imwrite("results/"+path_image, final)


    def scanDocuments(self, images, show=False):
        if show:
            self.__show = True
            for image in images:
                self.scanDocument(image)
        else:
            self.__show = False
            with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                try:
                    {executor.submit(self.scanDocument, image): image for image in images}
                except Exception as e:
                    raise e


scanner = DocScanner()
scanner.scanDocument("doc9.jpg", True)
#documents = os.listdir("Material Documentos")
#documents = ["doc3.jpg", "doc4.jpg", "doc5.jpg", "doc7.jpg", "doc9.jpg", "doc11.jpg", "doc12.jpg", "doc15.jpg"]
#scanner.scanDocuments(documents)#, True)
